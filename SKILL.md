# Skill: donghak-descendant-check

동학농민혁명 참여자 명부(cdpr.go.kr)를 조회해, 이용자가 참여 인물의 후손인지
판별을 돕는 **범용 조사 스킬**입니다.

## When to use
- 이용자가 "내 조상이 동학혁명(1894)에 참여했는지", "유족 등록이 가능한지" 묻는 경우.
- 성씨 / 본관 / 집성촌 / 파는 이용자마다 다르므로 **자유 입력**으로 처리.

## How to invoke (any agent)
핵심은 제로의존 Python CLI. 어떤 에이전트(Claude Code / Codex / Hermes 등)든
셸에서 동일하게 실행 가능합니다:

```bash
python donghak.py --surname <성씨> [--region <지역>] [--branch <파>] [--ancestor <실명>] [--clan <프리셋>]
```

예시:
```bash
python donghak.py --clan 수원백씨 --region 거창
python donghak.py --surname 김 --region 김해 --pages 50
python donghak.py --surname 백 --ancestor 백도홍
```

## Steps (agent가 따라야 할 절차)
1. **규모 탐지** — 스크립트가 총 페이지 수를 먼저 stderr 로 보고.
2. **순차 처리** — 페이지별 fetch+parse, 진행로그(`page k/M`) 출력.
3. **필터** — `--region` / `--branch` 로 좁힘. `common` 규모 성씨(김해김씨 등)는
   반드시 지역 필터를 권장(전수가 수천 건일 수 있음).
4. **결과 해석** — 매칭 0명 ≠ "참여 안 함". 미신청 / 이명(異名) / 실명 미상 가능성 열림.
   반드시 가족(부모님) → 종중 족보 → 기념재단(063-530-9434) 교차검증 안내.

## Notes
- 공식 API 없음(일반 PHP 폼). GET 방법 A(`commit/?menu=231&sf=all&sv=<성씨>&pno=N`) 사용.
- DB는 성씨+이름+지역만 보유, 본관/파 필터 불가 → 정직한 후보 제시가 목적.
- `--pages` 상한을 걸어도 "처리 k/M — 남은 N페이지 있음"을 명시해 놓침 방지.
- 외부 의존성 0개 (Python 표준라이브러리만). `python3 -m venv .venv` 격리 권장.
