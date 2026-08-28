#!/usr/bin/env python3
"""동학농민혁명 참여자 후손 판별 CLI (zero-dependency).

어떤 에이전트(Claude Code / Codex / Hermes 등)에서도 동작하도록
표준라이브러리만으로 작성. 범용 인터페이스:

  python donghak.py --surname 백 --region 거창
  python donghak.py --clan 수원백씨 --region 거창
  python donghak.py --surname 김 --region 김해 --pages 50
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from html.parser import HTMLParser

BASE_URL = "https://cdpr.go.kr/commit/"
SEARCH_TMPL = "?menu=231&sf=all&sv={sv}&pno={pno}"
UA = "Mozilla/5.0 (compatible; donghak-skill/1.0)"

GUIDE = """=== 동학 참여자 후손 찾기 — 시작 체크리스트 ===
막연하시다면 아래 순서로 준비하세요. 준비된 항목을 에이전트에 알려주시면
알맞은 조회 명령을 실행해 참여자와의 연결고리를 찾습니다.

1. 성씨 (필수): 백 / 이 / 김 / 변 등 — 어느 성씨 조상인가요?
2. 본관·파: 수원백씨 정신재공파 등 (모르면 '모름'이라도 OK)
3. 집성촌·연고 지역: 거창 도평리, 김해 등 — 가문이 뿌리내린 곳
4. 조상 실명: 부모님/가족이 아는 조상 이름 (있으면 바로 정확 매칭)
5. 항렬(돌림): 형(亨) 등 — 같은 항렬선 후보를 세대순으로 배열
6. 문헌/족보 파일: 컴퓨터에 있는 논문·족보 txt/html 경로 (있으면 미등록 후보 발굴)

--- 권장 흐름 ---
① 가족(부모님)께 조상 실명·본관·항렬 물어보기
② 위 항목을 에이전트에 전달 → DB 전수 조회
③ 논문/족보(--lit)로 미등록 참여자 교차검증
④ 기념재단(063-530-9434) 역조사 / 유족 등록 가능성 확인

⚠ 정직: 명부 DB는 성씨+이름+지역만 보유 → "후보 제시 + 교차검증"이 목적.
  "후손 확정"은 자동 주장하지 않습니다."""


def print_guide():
    print(GUIDE)

NAME_RE = re.compile(
    r"(?<![가-힣])([가-힣]{2,4})(?:\(([가-힣\u4e00-\u9fff]{1,5})\)|\s*([\u4e00-\u9fff]{1,5}))"
)

# 흔한 성씨의 한자 표기. DB가 한자형으로만 저장된 레코드(예: 백씨 '白道弘')를
# 잡기 위해, 대상 성씨의 한자로 시작하는 한자 이름을 보완 추출한다.
SURNAME_HANJA = {
    "백": "白", "이": "李", "김": "金", "변": "邊", "박": "朴", "최": "崔",
    "정": "鄭", "강": "姜", "조": "趙", "윤": "尹", "장": "張", "임": "林",
    "한": "韓", "오": "吳", "서": "徐", "신": "申", "권": "權", "황": "黃",
    "안": "安", "송": "宋", "유": "柳", "류": "柳", "홍": "洪", "전": "全",
    "고": "高", "문": "文", "양": "梁", "손": "孫", "심": "沈", "허": "許",
    "남": "南", "배": "裵", "석": "石", "윤": "尹", "구": "具", "차": "車",
}
PNO_RE = re.compile(r"pno=(\d+)")
DATE_RE = re.compile(r"(\d{4}[-.]\d{2}[-.]\d{2})")
REGION_RE = re.compile(r"^[가-힣]+도\s*[가-힣]*$")

_SSL_WARNED = False


def _open(req, timeout):
    """SSL 인증서 저장소가 없는 환경 대비: 검증 실패 시 비검증 컨텍스트로 재시도."""
    global _SSL_WARNED
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            if not _SSL_WARNED:
                print("[경고] SSL 인증서 검증 실패 → 비검증 컨텍스트로 재시도합니다.", file=sys.stderr)
                _SSL_WARNED = True
            ctx = ssl._create_unverified_context()
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        raise


@dataclass
class Participant:
    name_kr: str
    name_hanja: str
    region: str = ""
    content: str = ""
    registered: str = ""
    generation: str = ""        # 항렬 자 (라인 변경 시 채워짐)
    gen_index: int | None = None  # 항렬 순번 (세대 배열용)
    gen_abs: int | None = None    # 절대 세대(예: 경주이씨 대동항렬 39세)
    source: str = "DB"          # "DB" | "문헌"


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []
        self._buf = ""

    def handle_data(self, data):
        self._buf += data

    def handle_endtag(self, tag):
        if tag in ("p", "div", "br", "li", "tr"):
            text = self._buf.strip()
            if text:
                self.lines.append(text)
            self._buf = ""

    def get_lines(self) -> list[str]:
        tail = self._buf.strip()
        if tail:
            self.lines.append(tail)
        return self.lines


def _to_text(html: str) -> str:
    """HTML 을 태그가 제거된 평문(줄바꿈 보존)으로 변환."""
    p = _TextExtractor()
    p.feed(html)
    return "\n".join(p.get_lines())


def extract_records(html: str, surname_hanja: str | None = None) -> list[Participant]:
    """HTML 본문에서 참여자 레코드를 추출한다.

    이름 패턴(한글명(한자명))을 전체 텍스트에 대해 찾고, 각 이름 뒤 컨텍스트
    윈도우(다음 이름 출현 전까지)에서 지역/등록일을 추출. 동명(성씨+한자) 레코드
    는 페이지 내에서 합친다(페이지 간 중복제거는 호출자가 수행).

    surname_hanja 가 주어지면, DB가 한자형으로만 저장된 레코드(예: '白道弘')를
    잡기 위해 해당 한자로 시작하는 한자 이름도 보완 추출한다.
    """
    text = _to_text(html)
    recs: list[Participant] = []
    seen: set = set()
    matches = list(NAME_RE.finditer(text))
    for i, m in enumerate(matches):
        nxt = matches[i + 1].start() if i + 1 < len(matches) else m.end() + 300
        window = text[m.end(): nxt]
        name_kr, name_hanja = m.group(1), (m.group(2) or m.group(3))
        region = ""
        reg = re.search(r"참여지역[:\s]*([가-힣]+(?:도|시|군)\s*[가-힣]+)", window)
        if reg:
            region = reg.group(1)
        else:
            reg2 = REGION_RE.search(window)
            if reg2:
                region = reg2.group(0)
        d = DATE_RE.search(window)
        registered = d.group(1) if d else ""
        key = (name_kr, name_hanja)
        if key in seen:
            continue
        seen.add(key)
        recs.append(Participant(name_kr, name_hanja, region, window.strip(), registered))

    if surname_hanja:
        hre = re.compile(r"(?<![\u4e00-\u9fff])" + re.escape(surname_hanja) + r"[\u4e00-\u9fff]{1,4}")
        for m in hre.finditer(text):
            hanja = m.group(0)
            if hanja in seen:
                continue
            window = text[m.end(): m.end() + 300]
            region = ""
            reg = re.search(r"참여지역[:\s]*([가-힣]+(?:도|시|군)\s*[가-힣]+)", window)
            if reg:
                region = reg.group(1)
            else:
                reg2 = REGION_RE.search(window)
                if reg2:
                    region = reg2.group(0)
            d = DATE_RE.search(window)
            registered = d.group(1) if d else ""
            seen.add(hanja)
            recs.append(Participant("", hanja, region, window.strip(), registered))
    return recs


def detect_total_pages(html: str) -> int:
    """페이지네이션 링크에서 최대 pno 를 찾는다."""
    nums = [int(x) for x in PNO_RE.findall(html)]
    return max(nums) if nums else 1


def filter_records(records, region_filters, branch):
    out = []
    for r in records:
        blob = f"{r.region} {r.content}"
        if region_filters and not any(k in blob for k in region_filters):
            continue
        if branch and branch not in blob and branch not in r.name_kr:
            continue
        out.append(r)
    return out


def match_ancestor(records, ancestor: str) -> list[Participant]:
    a = ancestor.strip()
    return [r for r in records if r.name_kr == a or r.name_hanja == a]


def load_lineage(clan: str) -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clans.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f).get(clan, {})
    return {
        "generations": data.get("generations", []),
        "gen_start": data.get("gen_start", 1),
        "branches": data.get("branches", {}),
        "lineage_note": data.get("lineage_note", ""),
    }


def build_gen_map(generations, gen_start):
    """항렬 자 → [(리스트인덱, 절대세대), ...] 매핑. 같은 자가 여러 세대에 반복될 수 있음."""
    m: dict[str, list] = {}
    for i, item in enumerate(generations):
        chars = item if isinstance(item, list) else list(item)
        for c in chars:
            m.setdefault(c, []).append((i, gen_start + i))
    return m


def annotate_generation(records, generations, gen_start=1):
    """이름(성 제외)에 항렬 자가 포함되면 세대를 배정한다."""
    gmap = build_gen_map(generations, gen_start) if generations else {}
    for r in records:
        hit = None
        gi = ga = None
        for c in r.name_kr[1:]:
            if c in gmap:
                hit = c
                gi, ga = gmap[c][0]
                break
        r.generation = hit or ""
        r.gen_index = gi
        r.gen_abs = ga
    return records


def _edit_distance(a: str, b: str) -> int:
    """한글 1음절 단위 레벤슈타인 거리 (짧은 이름용)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, lb + 1):
            cur = dp[j]
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = cur
    return dp[lb]


def participant_band(birth_year: int, base: int = 1880, interval: int = 30, spread: int = 4):
    """앵커 출생년도 → 참여자(1894 운동 시 성년, 출생 추정 ~1880)의 절대 세대 밴드 추정.

    앵커와 참여자 사이 세대 간격(gen_gap)도 함께 반환.
    base=1880: 참여자 출생 추정년(1894 운동 당시 성년 기준).
    """
    gen_gap = max(1, round((birth_year - base) / interval))
    center = 40  # 1894년 전후 참여자가 사용한 경주이씨 대동항렬 세대 중심(문헌: 37~44세)
    return [center - spread, center + spread], gen_gap


KINSHIP = {1: "아버지", 2: "할아버지", 3: "증조할아버지", 4: "고조할아버지", 5: "5대 조상"}


def score_candidates(recs, gmap, band, region, ancestor):
    """DB 후보를 (항렬 세대 부합 + 연고지 + 조상실명 유사도)로 점수화.

    반환: [(점수, 레코드, 근거리스트), ...] 내림차순. 점수는 '상대적 가능성' 지표.
    """
    scored = []
    for r in recs:
        s = 0
        reasons = []
        if region and region in r.region:
            s += 3
            reasons.append("연고지 일치")
        if r.generation and gmap.get(r.generation):
            in_band = [g for _, g in gmap[r.generation] if band[0] <= g <= band[1]]
            if in_band:
                s += 1
                reasons.append(f"항렬 세대({in_band[0]}세)가 참여자 시기({band[0]}~{band[1]}세) 부합(참여자 자격 재확인 — 혈통 근거 아님)")
            else:
                reasons.append(f"항렬 세대({r.gen_abs}세)가 참여자 시기와 거리 있음(참여자 아닐 수 있음)")
        if ancestor:
            d = _edit_distance(ancestor, r.name_kr)
            if d <= 2:
                s += (3 - d)
                reasons.append(f"조상실명({ancestor})과 편집거리 {d}")
        if not reasons:
            reasons.append("연고/항렬/실명 근거 없음")
        scored.append((s, r, reasons))
    scored.sort(key=lambda x: -x[0])
    return scored


def group_by_generation(records, generations):
    if not generations:
        return {}
    out: dict[str, list[Participant]] = {g: [] for g in generations}
    out["기타"] = []
    for r in records:
        if r.generation and r.generation in out:
            out[r.generation].append(r)
        else:
            out["기타"].append(r)
    return {k: v for k, v in out.items() if v}


def scan_corpus(corpus_dir: str, surname: str | None = None) -> list[Participant]:
    """로컬 논문/족보/향토사 텍스트(.txt/.md/.html/.csv)에서 후보를 추출한다."""
    exts = (".txt", ".md", ".html", ".htm", ".csv")
    recs: list[Participant] = []
    seen: set[tuple[str, str]] = set()
    for root, _, files in os.walk(corpus_dir):
        for fn in files:
            if not fn.lower().endswith(exts):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    raw = fh.read()
            except OSError:
                continue
            text = _to_text(raw) if fn.lower().endswith((".html", ".htm")) else raw
            for m in NAME_RE.finditer(text):
                nk, nh = m.group(1), (m.group(2) or m.group(3))
                if surname and not nk.startswith(surname):
                    continue
                key = (nk, nh)
                if key in seen:
                    continue
                seen.add(key)
                recs.append(Participant(nk, nh, source="문헌"))
    return recs


def web_query(surname: str) -> list[Participant]:
    """환경변수 DONGHAK_LIT_API_URL(+KEY) 설정 시 웹 학술 검색. 미설정 시 [].

    URL 템플릿은 '{q}' 를 성씨로 치환. 응답은 HTML/텍스트로 간주해 추출.
    """
    url_tmpl = os.environ.get("DONGHAK_LIT_API_URL")
    if not url_tmpl:
        return []
    key = os.environ.get("DONGHAK_LIT_API_KEY", "")
    url = url_tmpl.replace("{q}", urllib.parse.quote(surname))
    headers = {"User-Agent": UA, "Authorization": f"Bearer {key}"} if key else {"User-Agent": UA}
    req = urllib.request.Request(url, headers=headers)
    try:
        with _open(req, 20) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except Exception:
        return []
    recs: list[Participant] = []
    seen: set[tuple[str, str]] = set()
    for m in NAME_RE.finditer(_to_text(html)):
        nk, nh = m.group(1), (m.group(2) or m.group(3))
        if not nk.startswith(surname):
            continue
        k = (nk, nh)
        if k in seen:
            continue
        seen.add(k)
        recs.append(Participant(nk, nh, source="웹"))
    return recs


def fetch_page(sv: str, pno: int, cache_dir: str, use_cache: bool = True) -> str:
    """sv: 실제 검색어(성씨 한글 또는 한자). 캐시는 sv+pno 별로 구분."""
    q = urllib.parse.quote(sv)
    url = BASE_URL + SEARCH_TMPL.format(sv=q, pno=pno)
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"page_{urllib.parse.quote(sv, safe='')}_{pno}.html")
    if use_cache and os.path.exists(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with _open(req, 20) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except Exception as e:  # network / timeout
        raise RuntimeError(f"페이지 {pno} 다운로드 실패: {e}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return html


def probe_scale(sv: str, cache_dir: str) -> tuple[int, int]:
    """pno=1 조회 → (총페이지, 추출레코드수)."""
    html = fetch_page(sv, 1, cache_dir)
    pages = detect_total_pages(html)
    return pages, len(extract_records(html))


def load_clan(key: str) -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clans.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f).get(key, {})


def build_report(surname, total_pages, processed, recs, matched, filters,
                 generations=None, lineage_note="", lit_only=None,
                 ranked=None, birth_year=None, gen_gap=None, anchor_label=None):
    lines = []
    lines.append("=== 동학농민혁명 참여자 조사 보고서 ===")
    lines.append(f"성씨: {surname}")
    lines.append(f"커버리지: 처리 {processed}/{total_pages} 페이지")
    if processed < total_pages:
        lines.append(f"⚠ 남은 {total_pages - processed} 페이지 있음 — --pages 값을 높여 재실행하세요.")
    lines.append(f"추출 레코드(공식DB): {len(recs)}건")
    if filters:
        lines.append(f"적용 필터: {', '.join(filters)}")
    for r in recs:
        gen = f" [{r.generation}]" if r.generation else ""
        disp = r.name_kr if r.name_kr else r.name_hanja
        han = f"({r.name_hanja})" if r.name_kr else ""
        lines.append(f"- {disp}{han}{gen} | {r.region} | {r.registered}")
    if matched:
        lines.append(f"★ 조상 매칭 후보: {', '.join(r.name_kr for r in matched)}")
    if generations:
        lines.append("")
        lines.append("--- 항렬 기반 세대 배열 ---")
        grp = group_by_generation(recs, generations)
        for g, items in grp.items():
            names = ", ".join(r.name_kr for r in items)
            lines.append(f"  항렬 '{g}': {names if names else '(해당 없음)'}")
        if lineage_note:
            lines.append(f"  ※ {lineage_note}")
    if lit_only:
        lines.append("")
        lines.append("--- 문헌/논문에서 추가 발굴 (공식DB에 없는 후보) ---")
        for r in lit_only:
            lines.append(f"- {r.name_kr}({r.name_hanja}) [{r.source}]")
        lines.append("  ※ 이들은 미등록 참여자일 가능성 — 종중/기념재단 교차검증 권장")
    if ranked is not None:
        lines.append("")
        lines.append("--- 세대 역산 + 가능성 순위 (상대 점수) ---")
        if birth_year and gen_gap is not None:
            who = anchor_label or f"조회자({birth_year})"
            kin = KINSHIP.get(gen_gap, f"{gen_gap}대 위")
            lines.append(f"  {who} 기준 → 참여자는 약 {gen_gap}대 위({kin} 세대) 가능성")
        for s, r, reasons in ranked[:10]:
            disp = r.name_kr if r.name_kr else r.name_hanja
            han = f"({r.name_hanja})" if r.name_kr else ""
            gen = f" {r.generation}({r.gen_abs}세)" if r.generation else ""
            lines.append(f"  [{s}점] {disp}{han}{gen} | {r.region} | 이유: {', '.join(reasons)}")
        lines.append("  ※ 점수는 항렬 세대부합+연고지+실명유사도의 상대지표. '확정' 아님.")
        lines.append("  ※ 증조할아버지 실명을 모르는 경우가 많음 — 위 후보 중 집안(부모님/친척/족보)에서")
        lines.append("    들은 적 있는 이름이 있는지 가족에게 확인하세요. 있으면 그 인물이 연결고리 후보.")
    lines.append("")
    lines.append("권장 다음단계: 1)부모님께 실명 확인 2)종중 족보 문의 3)기념재단(063-530-9434) 역조사/유족등록")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="동학농민혁명 참여자 후손 판별")
    ap.add_argument("--surname", help="성씨 (예: 백)")
    ap.add_argument("--bonghan", help="본관 (예: 수원백씨)")
    ap.add_argument("--region", help="집성촌/연고 지역 키워드")
    ap.add_argument("--branch", help="파(派) (예: 정신재공파)")
    ap.add_argument("--ancestor", help="부모님 확인 조상 실명")
    ap.add_argument("--clan", help="clans.json 프리셋 키")
    ap.add_argument("--pages", type=int, default=0, help="최대 처리 페이지 (0=전체)")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--csv", action="store_true", help="CSV 출력")
    ap.add_argument("--out", help="결과 저장 파일")
    ap.add_argument("--no-cache", action="store_true", help="캐시 미사용(항상 재다운로드)")
    ap.add_argument("--lit", help="로컬 문헌/논문 코퍼스 폴더 (미등록 후보 추가 발굴)")
    ap.add_argument("--web", action="store_true", help="웹 학술검색 사용(.env DONGHAK_LIT_API_URL/KEY 필요)")
    ap.add_argument("--guide", action="store_true", help="사용자용 시작 체크리스트 출력")
    ap.add_argument("--birth-year", type=int, help="조회자 출생년도 (세대 역산·가능성 순위용)")
    ap.add_argument("--ancestor-birth-year", type=int, help="조상(--ancestor) 출생년도 (세대 앵커로 쓰면 더 타이트)")
    ap.add_argument("--gen-interval", type=int, default=30, help="세대 간 연수 (기본 30)")
    args = ap.parse_args(argv)

    if args.guide:
        print_guide()
        return

    preset = load_clan(args.clan) if args.clan else {}
    surname = args.surname or preset.get("surname")
    if not surname:
        ap.error("--surname 또는 --clan 이 필요합니다")
    surname_hanja = SURNAME_HANJA.get(surname)
    region_filters: list[str] = []
    if args.region:
        region_filters = [args.region]

    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
    # 한글 성씨 + 한자 성씨 둘 다 조회(사이트가 한자형으로만 저장한 레코드 대비)
    queries = [surname]
    if surname_hanja:
        queries.append(surname_hanja)
    total_pages = max(probe_scale(q, cache_dir)[0] for q in queries)
    print(f"[규모] 성씨 '{surname}'({surname_hanja or '-'}): 총 {total_pages}페이지", file=sys.stderr)
    if not region_filters and preset.get("scale") == "common":
        print("[안내] 대규모 성씨입니다 — --region 으로 범위를 좁히면 빠르고 정확합니다.", file=sys.stderr)

    limit = args.pages if args.pages > 0 else total_pages
    all_recs: list[Participant] = []
    for p in range(1, limit + 1):
        for q in queries:
            try:
                html = fetch_page(q, p, cache_dir, use_cache=not args.no_cache)
            except RuntimeError as e:
                print(f"[경고] {e}", file=sys.stderr)
                break
            all_recs.extend(extract_records(html, surname_hanja))
        print(f"[진행] page {p}/{limit} ({len(all_recs)}건)", file=sys.stderr)

    all_recs = [
        r for r in all_recs
        if r.name_kr.startswith(surname)
        or (surname_hanja and r.name_hanja.startswith(surname_hanja))
    ]
    seen_g: set[tuple[str, str]] = set()
    dedup: list[Participant] = []
    for r in all_recs:
        k = r.name_hanja or r.name_kr
        if k in seen_g:
            continue
        seen_g.add(k)
        dedup.append(r)
    all_recs = dedup

    # A. 항렬/종파: 이름에 항렬 자가 있으면 세대 배정
    lineage = load_lineage(args.clan) if args.clan else {}
    generations = lineage.get("generations", [])
    gen_start = lineage.get("gen_start", 1)
    lineage_note = lineage.get("lineage_note", "")
    annotate_generation(all_recs, generations, gen_start)

    # B. 문헌/논문: 로컬 코퍼스 + 선택 웹 → 공식DB에 없는 후보 발굴
    extra_recs: list[Participant] = []
    if args.lit:
        extra_recs.extend(scan_corpus(args.lit, surname))
        print(f"[문헌] '{args.lit}' 스캔 → 후보 {len(extra_recs)}건", file=sys.stderr)
    if args.web:
        web_recs = web_query(surname)
        print(f"[웹] 학술검색 → 후보 {len(web_recs)}건", file=sys.stderr)
        extra_recs.extend(web_recs)
    official_names = {r.name_kr for r in all_recs}
    lit_only = [r for r in extra_recs if r.name_kr not in official_names]

    if region_filters or args.branch:
        recs = filter_records(all_recs, region_filters, args.branch)
    else:
        recs = all_recs
    matched = match_ancestor(all_recs, args.ancestor) if args.ancestor else []

    # 세대 역산 + 가능성 순위 (조회자 출생년도 + clan 항렬표 있으면)
    ranked = None
    gen_gap = None
    anchor_label = None
    if args.birth_year and generations:
        # 앵커: 조상 출생년도가 있으면 그것을 기준(더 타이트), 없으면 조회자 본인
        anchor = args.ancestor_birth_year or args.birth_year
        interval = args.gen_interval
        if args.birth_year and args.ancestor_birth_year:
            # 두 세대 생년 차이로 실제 세대 간격 추정 (아버지-자식 = 1세대)
            interval = max(1, args.birth_year - args.ancestor_birth_year)
        band, gen_gap = participant_band(anchor, interval=interval)
        gmap = build_gen_map(generations, gen_start)
        ranked = score_candidates(recs, gmap, band, args.region, args.ancestor)
        if args.ancestor_birth_year:
            anchor_label = f"이상만({args.ancestor_birth_year})"
        else:
            anchor_label = f"조회자({args.birth_year})"

    report = build_report(
        surname, total_pages, min(limit, total_pages), recs, matched,
        region_filters + ([args.branch] if args.branch else []),
        generations=generations, lineage_note=lineage_note, lit_only=lit_only,
        ranked=ranked, birth_year=args.birth_year, gen_gap=gen_gap,
        anchor_label=anchor_label,
    )
    if args.json:
        out = json.dumps([asdict(r) for r in recs], ensure_ascii=False, indent=2)
    elif args.csv:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["name_kr", "name_hanja", "region", "content", "registered"])
        for r in recs:
            w.writerow([r.name_kr, r.name_hanja, r.region, r.content, r.registered])
        out = buf.getvalue()
    else:
        out = report
    if args.out:
        open(args.out, "w", encoding="utf-8").write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
