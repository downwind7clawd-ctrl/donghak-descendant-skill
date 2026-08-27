# 동학농민혁명 참여자 후손 판별 스킬 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 어떤 에이전트(Codex/Claude Code/Hermes)에서도 `python donghak.py` 로 동학농민혁명 참여자 후손 여부를 조사할 수 있는 제로의존 단일 스크립트 + 래퍼 문서를 만든다.

**Architecture:** `donghak.py`(stdlib only)가 cdpr.go.kr 를 GET 으로 조회→HTML 파싱→지역/파/조상 필터→보고서 생성. `clans.json`은 선택 프리셋. `SKILL.md`/`README.md`는 범용 사용법 문서. 오프라인 `tests/` 로 파서 검증.

**Tech Stack:** Python 3.12 표준라이브러리만 (urllib, re, html.parser, json, argparse, csv, unittest). 외부 의존성 0.

---

## File Structure

- `donghak.py` — 핵심 CLI (신규, zero-dep)
- `clans.json` — 선택 프리셋 (신규)
- `SKILL.md` — 에이전트 래퍼 (신규)
- `README.md` — 범용 사용법 + 특별법 맥락 (신규)
- `tests/sample_page.html` — 오프라인 파서 픽스처 (신규)
- `tests/test_parse.py` — stdlib unittest (신규)
- `LOG/2026-08-27.md` — 작업 로그 (신규)
- `.gitignore` — 이미 존재 (수정 없음)
- `docs/superpowers/specs/2026-08-27-donghak-descendant-skill-design.md` — 설계 명세 (이미 커밋됨)

---

### Task 1: 파서 테스트 픽스처 + 실패 테스트 작성

**Files:**
- Create: `tests/sample_page.html`
- Create: `tests/test_parse.py`

- [ ] **Step 1: 오프라인 픽스처 작성** (`tests/sample_page.html`)

실제 cdpr 응답과 유사한 비식별 구조. 이름 패턴 `한글명(한자명)` + 지역 + 내용 + 등록일 포함.

```html
<html><body>
<div class="list">
<p>백도홍(白道弘)</p>
<p>1894년 경상도 하동에서 대접주로 활동 후 체포·처형</p>
<p>참여지역: 경상도 하동</p>
<p>등록일자: 2006-11-20</p>

<p>백홍석(白弘錫)</p>
<p>1894년 경상도 진주에서 참여 후 살해</p>
<p>참여지역: 경상도 진주</p>
<p>등록일자: 2007-03-15</p>

<p>김이순(金伊順)</p>
<p>전라도 무주에서 참여</p>
<p>참여지역: 전라도 무주</p>
<p>등록일자: 2008-01-09</p>

<p class="paging">pno=1 pno=2 pno=3</p>
</div></body></html>
```

- [ ] **Step 2: 실패 테스트 작성** (`tests/test_parse.py`)

```python
import os, unittest
from donghak import extract_records, detect_total_pages, filter_records

HERE = os.path.dirname(__file__)
HTML = open(os.path.join(HERE, "sample_page.html"), encoding="utf-8").read()

class TestParse(unittest.TestCase):
    def test_extract_three(self):
        recs = extract_records(HTML)
        self.assertEqual(len(recs), 3)

    def test_name_parts(self):
        recs = extract_records(HTML)
        by = {r.name_kr: r for r in recs}
        self.assertEqual(by["백도홍"].name_hanja, "白道弘")
        self.assertIn("하동", by["백도홍"].region)

    def test_detect_pages(self):
        self.assertEqual(detect_total_pages(HTML), 3)

    def test_filter_region(self):
        recs = extract_records(HTML)
        gyeong = filter_records(recs, ["경상"], None)
        self.assertEqual(len(gyeong), 2)
        jeolla = filter_records(recs, ["전라"], None)
        self.assertEqual(len(jeolla), 1)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 테스트 실행하여 실패 확인**

Run: `cd /home/david/nas_1tb/dev/Donghak && python -m unittest tests.test_parse -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'donghak'`)

- [ ] **Step 4: 커밋**

```bash
git add tests/sample_page.html tests/test_parse.py
git commit -m "test: 파서 오프라인 픽스처 및 실패 테스트 추가"
```

---

### Task 2: HTML 파서 + 페이지 수 탐지 구현

**Files:**
- Create: `donghak.py`

- [ ] **Step 1: 모듈 골격 + 파서 구현** (`donghak.py`)

```python
#!/usr/bin/env python3
"""동학농민혁명 참여자 후손 판별 CLI (zero-dependency).

Usage:
  python donghak.py --surname 백 --region 거창
  python donghak.py --clan 수원백씨 --region 거창
"""
from __future__ import annotations
import argparse
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

BASE_URL = "https://cdpr.go.kr/commit/"
SEARCH_TMPL = "?menu=231&sf=all&sv={sv}&pno={pno}"
UA = "Mozilla/5.0 (compatible; donghak-skill/1.0)"

NAME_RE = re.compile(r"([가-힣]+)\(([가-힣]+)\)")
PNO_RE = re.compile(r"pno=(\d+)")
DATE_RE = re.compile(r"(\d{4}[-.]\d{2}[-.]\d{2})")


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
        elif "참여지역" in line or any(
            k in line for k in ("도", "시", "군", "읍", "면", "리")
        ):
            cur.region = line.replace("참여지역", "").replace(":", "").strip()
        else:
            cur.content = (cur.content + " " + line).strip()
    if cur:
        recs.append(cur)
    return recs


def detect_total_pages(html: str) -> int:
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
```

- [ ] **Step 2: 테스트 실행하여 통과 확인**

Run: `python -m unittest tests.test_parse -v`
Expected: PASS (4 tests)

- [ ] **Step 3: 커밋**

```bash
git add donghak.py
git commit -m "feat: HTML 파서 및 페이지 수 탐지 구현"
```

---

### Task 3: HTTP fetch + 캐시 + 규모 탐지

**Files:**
- Modify: `donghak.py` (함수 추가)

- [ ] **Step 1: fetch/cache/scale 함수 추가**

`donghak.py` 하단(클래스/함수 뒤)에 추가:

```python
def fetch_page(surname: str, pno: int, cache_dir: str, use_cache: bool = True) -> str:
    sv = urllib.parse.quote(surname)
    url = BASE_URL + SEARCH_TMPL.format(sv=sv, pno=pno)
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"page_{pno}.html")
    if use_cache and os.path.exists(path):
        return open(path, encoding="utf-8", errors="ignore").read()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except Exception as e:  # network/timeout
        raise RuntimeError(f"페이지 {pno} 다운로드 실패: {e}")
    open(path, "w", encoding="utf-8").write(html)
    return html


def probe_scale(surname: str, cache_dir: str) -> tuple[int, int]:
    """pno=1 조회 → (총페이지, 추출레코드수) 반환."""
    html = fetch_page(surname, 1, cache_dir)
    pages = detect_total_pages(html)
    return pages, len(extract_records(html))
```

- [ ] **Step 2: fetch 단위 테스트 추가** (`tests/test_parse.py` 에 추가)

로컬 파일을 모킹하는 대신, 캐시 우선경로를 검증:

```python
import tempfile, shutil
from donghak import fetch_page

class TestFetch(unittest.TestCase):
    def test_cache_used(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "page_1.html")
            open(p, "w", encoding="utf-8").write(HERE and open(os.path.join(os.path.dirname(__file__), "sample_page.html"), encoding="utf-8").read())
            html = fetch_page("백", 1, d, use_cache=True)
            self.assertIn("백도홍", html)
        finally:
            shutil.rmtree(d)
```

- [ ] **Step 3: 테스트 실행**

Run: `python -m unittest tests.test_parse -v`
Expected: PASS (전체 5 tests)

- [ ] **Step 4: 커밋**

```bash
git add donghak.py tests/test_parse.py
git commit -m "feat: HTTP fetch + 캐시 + 규모 탐지"
```

---

### Task 4: 조상 매칭 + CLI 오케스트레이션 + 보고서

**Files:**
- Modify: `donghak.py`

- [ ] **Step 1: 매칭/리포트/클랜/메인 추가**

`donghak.py` 에 추가:

```python
def match_ancestor(records, ancestor: str) -> list[Participant]:
    a = ancestor.strip()
    return [r for r in records if r.name_kr == a or r.name_hanja == a]


def load_clan(key: str) -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clans.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f).get(key, {})


def build_report(surname, total_pages, processed, recs, matched, filters):
    lines = []
    lines.append(f"=== 동학농민혁명 참여자 조사 보고서 ===")
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
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--out", help="결과 저장 파일")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)

    preset = load_clan(args.clan) if args.clan else {}
    surname = args.surname or preset.get("surname")
    if not surname:
        ap.error("--surname 또는 --clan 이 필요합니다")
    region_filters = []
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

    report = build_report(surname, total_pages, min(limit, total_pages), recs, matched,
                          region_filters + ([args.branch] if args.branch else []))
    if args.json:
        out = json.dumps([asdict(r) for r in recs], ensure_ascii=False, indent=2)
    elif args.csv:
        import io
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
```

> 주의: `asdict` 사용을 위해 `from dataclasses import dataclass, asdict` 로 임포트 수정 필요 (Task 2 코드의 import 라인 갱신).

- [ ] **Step 2: CLI 스모크 테스트 (오프라인 캐시 사용)**

Run: `python donghak.py --surname 백 --no-cache false` 는 네트워크 필요 → 대신 로컬 캐시 시뮬:
```bash
mkdir -p cache && cp tests/sample_page.html cache/page_1.html
python donghak.py --surname 백 --pages 1 --region 경상
```
Expected: "추출 레코드: 2건" (백도홍, 백홍석), 커버리지 1/1.

- [ ] **Step 3: 커밋**

```bash
git add donghak.py
git commit -m "feat: 조상 매칭, CLI 오케스트레이션, 보고서 생성"
```

---

### Task 5: clans.json 프리셋 + 문서 (SKILL.md, README.md, LOG)

**Files:**
- Create: `clans.json`
- Create: `SKILL.md`
- Create: `README.md`
- Create: `LOG/2026-08-27.md`

- [ ] **Step 1: clans.json 작성**

```json
{
  "수원백씨": {
    "surname": "백",
    "region_keywords": ["거창","경남","경상","함안","합천","산청","창녕","의령","밀양","거제","통영","고성","남해","하동","진주"],
    "generations": ["남","기","규","형","흠","인","낙","창"],
    "scale": "rare"
  },
  "경주이씨": {
    "surname": "이",
    "region_keywords": ["경주","성주","경상","영남"],
    "scale": "common"
  },
  "김해김씨": {
    "surname": "김",
    "region_keywords": ["김해","밀양","경남"],
    "scale": "common"
  },
  "초계변씨": {
    "surname": "변",
    "region_keywords": ["초계","거창","경남"],
    "scale": "mid"
  }
}
```

- [ ] **Step 2: SKILL.md 작성**

```markdown
# Skill: donghak-descendant-check

동학농민혁명 참여자 명부(cdpr.go.kr)를 조회해, 이용자가 참여 인물의 후손인지
판별을 돕는 범용 조사 스킬.

## When to use
- 이용자가 "내 조상이 동학혁명에 참여했는지", "유족 등록이 가능한지" 묻는 경우.
- 성씨/본관/집성촌/파 는 이용자마다 다르므로 자유 입력.

## How to invoke (any agent)
핵심은 제로의존 Python CLI. 어떤 에이전트든 셸에서 실행:
  python donghak.py --surname <성씨> [--region <지역>] [--branch <파>] [--ancestor <실명>] [--clan <프리셋>]
예시:
  python donghak.py --clan 수원백씨 --region 거창
  python donghak.py --surname 김 --region 김해 --pages 50

## Steps
1. 규모 탐지: 스크립트가 총 페이지 수를 먼저 보고.
2. 순차 처리: 페이지별 fetch+parse, 진행로그 출력.
3. 필터: --region/--branch 로 좁힘. common 성씨는 반드시 지역 필터 권장.
4. 결과 해석: 매칭 0명 ≠ 참여 안 함(미신청/이명 가능). 가족/종중/기념재단 교차검증 안내.

## Notes
- 공식 API 없음(일반 PHP 폼). GET 방법 A 사용.
- DB는 성씨+이름+지역만 보유, 본관/파 필터 불가 → 정직한 후보 제시.
```

- [ ] **Step 3: README.md 작성**

특별법 맥락(동학농민혁명 참여자 등의 명예회복에 관한 특별법, 2017 공포/2018 시행, 시행령 2026.1.2), 설치(venv), 범용 실행법(에이전트별), 전체 CLI 플래그, 레퍼런스 성씨 4종, 한계 명시.

- [ ] **Step 4: LOG/2026-08-27.md 작성**

세션 작업 요약(설계→계획→구현→테스트) 기록. 발신자: 맥거핀.

- [ ] **Step 5: 커밋**

```bash
git add clans.json SKILL.md README.md LOG/2026-08-27.md
git commit -m "docs: clans 프리셋, SKILL.md, README, 작업 로그 추가"
```

---

### Task 6: 사후 테스트 (Live Sandbox E2E) + 검증 기록

**Files:**
- Modify: `LOG/2026-08-27.md` (검증 결과 추가)

- [ ] **Step 1: 백씨 전수 라이브 조회**

Run: `python donghak.py --clan 수원백씨 --region 거창`
Expected: 백씨 전체 추출(초기조사 기준 45건), 거창/함안 연고 0건 재현, 커버리지 14/14.

- [ ] **Step 2: --pages 상한 경고 검증**

Run: `python donghak.py --clan 수원백씨 --pages 2`
Expected: "커버리지: 처리 2/14 페이지 ⚠ 남은 12 페이지 있음" 경고 출력.

- [ ] **Step 3: 결과를 LOG 에 기록**

`LOG/2026-08-27.md` 에 실제 추출 건수/결과 요약 추가.

- [ ] **Step 4: 최종 커밋**

```bash
git add LOG/2026-08-27.md
git commit -m "test: 라이브 E2E 사후 테스트 결과 기록"
```

---

## Self-Review (작성자 체크)

1. **Spec coverage:** §2 범용성(제로의존 CLI+SKILL.md)→Task2/4/5. §3 격리환경(venv/gitignore)→이미 초기화+Task5 문서. §4 CLI→Task4. §5 데이터흐름(규모탐지→순차→필터→보고서)→Task3/4. §6 파싱→Task2. §7 에러처리→Task3 fetch 재시도/경고. §8 테스트→Task1/3/6. §9 문서→Task5. §10 GitHub→구현 후 push(사용자 확인). §11 한계→SKILL.md/README. 모두 태스크 매핑됨.
2. **Placeholder scan:** 샘플 코드 모두 실제 내용 포함. "TBD/유사하게" 없음.
3. **Type consistency:** `Participant`(Task2 정의, Task4에서 `asdict(r)`/`match_ancestor` 재사용), `extract_records`/`detect_total_pages`/`filter_records` 시그니처 Task1~4 일관. `fetch_page(surname,pno,cache_dir,use_cache)` Task3 정의→Task4 호출 일치.
