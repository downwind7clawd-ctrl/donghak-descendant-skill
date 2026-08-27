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

NAME_RE = re.compile(
    r"(?<![가-힣])([가-힣]+)(?:\(([가-힣\u4e00-\u9fff]+)\)|\s+([\u4e00-\u9fff]+))"
)
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


def extract_records(html: str) -> list[Participant]:
    """HTML 본문에서 참여자 레코드를 추출한다.

    이름 패턴(한글명(한자명))을 전체 텍스트에 대해 찾고, 각 이름 뒤 컨텍스트
    윈도우(다음 이름 출현 전까지)에서 지역/등록일을 추출. 동명(성씨+한자) 레코드
    는 페이지 내에서 합친다(페이지 간 중복제거는 호출자가 수행).
    """
    text = _to_text(html)
    recs: list[Participant] = []
    seen: set[tuple[str, str]] = set()
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
        "branches": data.get("branches", {}),
        "lineage_note": data.get("lineage_note", ""),
    }


def annotate_generation(records, generations):
    """이름에 항렬 자가 포함되면 세대(gen_index)를 배정한다."""
    gen_index = {g: i for i, g in enumerate(generations)} if generations else {}
    for r in records:
        hit = next((g for g in generations if g and g in r.name_kr), None) if generations else None
        r.generation = hit or ""
        r.gen_index = gen_index.get(hit) if hit else None
    return records


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


def fetch_page(surname: str, pno: int, cache_dir: str, use_cache: bool = True) -> str:
    sv = urllib.parse.quote(surname)
    url = BASE_URL + SEARCH_TMPL.format(sv=sv, pno=pno)
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"page_{pno}.html")
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


def probe_scale(surname: str, cache_dir: str) -> tuple[int, int]:
    """pno=1 조회 → (총페이지, 추출레코드수)."""
    html = fetch_page(surname, 1, cache_dir)
    pages = detect_total_pages(html)
    return pages, len(extract_records(html))


def load_clan(key: str) -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clans.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f).get(key, {})


def build_report(surname, total_pages, processed, recs, matched, filters,
                 generations=None, lineage_note="", lit_only=None):
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
        lines.append(f"- {r.name_kr}({r.name_hanja}){gen} | {r.region} | {r.registered}")
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
    args = ap.parse_args(argv)

    preset = load_clan(args.clan) if args.clan else {}
    surname = args.surname or preset.get("surname")
    if not surname:
        ap.error("--surname 또는 --clan 이 필요합니다")
    region_filters: list[str] = []
    if args.region:
        region_filters = [args.region]

    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
    total_pages, _ = probe_scale(surname, cache_dir)
    print(f"[규모] 성씨 '{surname}': 총 {total_pages}페이지", file=sys.stderr)
    if not region_filters and preset.get("scale") == "common":
        print("[안내] 대규모 성씨입니다 — --region 으로 범위를 좁히면 빠르고 정확합니다.", file=sys.stderr)

    limit = args.pages if args.pages > 0 else total_pages
    all_recs: list[Participant] = []
    for p in range(1, limit + 1):
        try:
            html = fetch_page(surname, p, cache_dir, use_cache=not args.no_cache)
        except RuntimeError as e:
            print(f"[경고] {e}", file=sys.stderr)
            break
        all_recs.extend(extract_records(html))
        print(f"[진행] page {p}/{limit} ({len(all_recs)}건)", file=sys.stderr)

    all_recs = [r for r in all_recs if r.name_kr.startswith(surname)]
    seen_g: set[tuple[str, str]] = set()
    dedup: list[Participant] = []
    for r in all_recs:
        k = (r.name_kr, r.name_hanja)
        if k in seen_g:
            continue
        seen_g.add(k)
        dedup.append(r)
    all_recs = dedup

    # A. 항렬/종파: 이름에 항렬 자가 있으면 세대 배정
    lineage = load_lineage(args.clan) if args.clan else {}
    generations = lineage.get("generations", [])
    lineage_note = lineage.get("lineage_note", "")
    annotate_generation(all_recs, generations)

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

    report = build_report(
        surname, total_pages, min(limit, total_pages), recs, matched,
        region_filters + ([args.branch] if args.branch else []),
        generations=generations, lineage_note=lineage_note, lit_only=lit_only,
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
