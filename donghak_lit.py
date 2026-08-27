#!/usr/bin/env python3
"""donghak_lit.py — 문헌/논문 기반 미등록 참여자 발굴 (제로의존).

용법:
  python donghak_lit.py --corpus <폴더> [--surname 백] [--web] [--json|--csv] [--out 파일]

- 로컬 코퍼스(기본): .txt/.md/.html/.csv 파일을 재귀 스캔해 성씨+이름 후보 추출
- 웹 모드(--web, 선택): 환경변수 DONGHAK_LIT_API_URL(+KEY) 설정 시에만 동작
"""
import argparse
import csv
import io
import json
import os
import sys

from donghak import NAME_RE, Participant, scan_corpus, web_query

UA = "Mozilla/5.0 (donghak-lit; +https://github.com/downwind7clawd-ctrl/donghak-skill)"


def main(argv=None):
    ap = argparse.ArgumentParser(description="문헌/논문 기반 동학 참여자 후보 발굴")
    ap.add_argument("--corpus", help="로컬 문헌/논문 폴더 (재귀 스캔)")
    ap.add_argument("--surname", help="성씨 (없으면 모든 성씨)")
    ap.add_argument("--web", action="store_true", help="웹 학술검색 (.env DONGHAK_LIT_API_URL/KEY 필요)")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--csv", action="store_true", help="CSV 출력")
    ap.add_argument("--out", help="결과 저장 파일")
    args = ap.parse_args(argv)

    if not args.corpus and not args.web:
        ap.error("--corpus 또는 --web 중 하나는 필요합니다")

    recs: list[Participant] = []
    if args.corpus:
        recs.extend(scan_corpus(args.corpus, args.surname))
        print(f"[문헌] '{args.corpus}' 스캔 → 후보 {len(recs)}건", file=sys.stderr)
    if args.web:
        web_recs = web_query(args.surname or "")
        print(f"[웹] 학술검색 → 후보 {len(web_recs)}건", file=sys.stderr)
        recs.extend(web_recs)

    if args.json:
        out = json.dumps([vars(r) for r in recs], ensure_ascii=False, indent=2)
    elif args.csv:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["name_kr", "name_hanja", "source"])
        for r in recs:
            w.writerow([r.name_kr, r.name_hanja, r.source])
        out = buf.getvalue()
    else:
        lines = [f"=== 문헌/논문 후보 ({len(recs)}건) ==="]
        for r in recs:
            lines.append(f"- {r.name_kr}({r.name_hanja}) [{r.source}]")
        out = "\n".join(lines)

    if args.out:
        open(args.out, "w", encoding="utf-8").write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
