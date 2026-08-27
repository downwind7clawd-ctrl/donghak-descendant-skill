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
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from html.parser import HTMLParser

BASE_URL = "https://cdpr.go.kr/commit/"
SEARCH_TMPL = "?menu=231&sf=all&sv={sv}&pno={pno}"
UA = "Mozilla/5.0 (compatible; donghak-skill/1.0)"

NAME_RE = re.compile(r"([가-힣]+)\(([가-힣\u4e00-\u9fff]+)\)")
PNO_RE = re.compile(r"pno=(\d+)")
DATE_RE = re.compile(r"(\d{4}[-.]\d{2}[-.]\d{2})")
REGION_RE = re.compile(r"^[가-힣]+도\s*[가-힣]*$")


@dataclass
class Participant:
    name_kr: str
    name_hanja: str
    region: str = ""
    content: str = ""
    registered: str = ""


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


def _to_lines(html: str) -> list[str]:
    p = _TextExtractor()
    p.feed(html)
    return p.get_lines()


def extract_records(html: str) -> list[Participant]:
    """HTML 본문에서 참여자 레코드를 추출한다."""
    lines = _to_lines(html)
    recs: list[Participant] = []
    cur: Participant | None = None
    for line in lines:
        m = NAME_RE.search(line)
        if m and (line.startswith(m.group(0)) or len(line) <= len(m.group(0)) + 4):
            if cur:
                recs.append(cur)
            cur = Participant(name_kr=m.group(1), name_hanja=m.group(2))
            continue
        if cur is None:
            continue
        if DATE_RE.search(line):
            cur.registered = DATE_RE.search(line).group(1)
        elif "참여지역" in line:
            cur.region = line.split("참여지역")[-1].replace(":", "").strip()
        elif REGION_RE.match(line.strip()):
            cur.region = line.strip()
        else:
            cur.content = (cur.content + " " + line).strip()
    if cur:
        recs.append(cur)
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
        with urllib.request.urlopen(req, timeout=20) as resp:
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


def build_report(surname, total_pages, processed, recs, matched, filters):
    lines = []
    lines.append("=== 동학농민혁명 참여자 조사 보고서 ===")
    lines.append(f"성씨: {surname}")
    lines.append(f"커버리지: 처리 {processed}/{total_pages} 페이지")
    if processed < total_pages:
        lines.append(f"⚠ 남은 {total_pages - processed} 페이지 있음 — --pages 값을 높여 재실행하세요.")
    lines.append(f"추출 레코드: {len(recs)}건")
    if filters:
        lines.append(f"적용 필터: {', '.join(filters)}")
    for r in recs:
        lines.append(f"- {r.name_kr}({r.name_hanja}) | {r.region} | {r.registered}")
    if matched:
        lines.append(f"★ 조상 매칭 후보: {', '.join(r.name_kr for r in matched)}")
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
    args = ap.parse_args(argv)

    preset = load_clan(args.clan) if args.clan else {}
    surname = args.surname or preset.get("surname")
    if not surname:
        ap.error("--surname 또는 --clan 이 필요합니다")
    region_filters: list[str] = []
    if args.region:
        region_filters = [args.region]
    elif args.clan and preset.get("region_keywords"):
        region_filters = preset["region_keywords"]

    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
    total_pages, _ = probe_scale(surname, cache_dir)
    print(f"[규모] 성씨 '{surname}': 총 {total_pages}페이지", file=sys.stderr)

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

    if region_filters or args.branch:
        recs = filter_records(all_recs, region_filters, args.branch)
    else:
        recs = all_recs
    matched = match_ancestor(all_recs, args.ancestor) if args.ancestor else []

    report = build_report(
        surname, total_pages, min(limit, total_pages), recs, matched,
        region_filters + ([args.branch] if args.branch else []),
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
