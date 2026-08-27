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
python donghak.py --surname <성씨> [--region <지역>] [--branch <파>] [--ancestor <실명>] [--clan <프리셋>] [--lit <문헌폴더>] [--web]
```

예시：
```bash
python donghak.py --clan 수원백씨 --region 거창
python donghak.py --surname 김 --region 김해 --pages 50
python donghak.py --surname 백 --ancestor 백도홍
python donghak.py --surname 백 --clan 수원백씨        # 항렬 세대 배열 포함
python donghak.py --surname 백 --lit ./corpus         # 문헌에서 미등록 후보 발굴
python donghak_lit.py --corpus ./corpus --surname 백  # 문헌 전용 독립 CLI
```

## Steps (agent가 따라야 할 절차)
1. **규모 탐지** — 스크립트가 총 페이지 수를 먼저 stderr 로 보고.
2. **순차 처리** — 페이지별 fetch+parse, 진행로그(`page k/M`) 출력.
3. **필터** — `--region` / `--branch` 로 좁힘. `common` 규모 성씨(김해김씨 등)는
   반드시 지역 필터를 권장(전수가 수천 건일 수 있음).
4. **항렬 교차검증** — `--clan` 프리셋이 있으면 이름의 항렬 자로 세대를 배정, 보고서에
   "항렬 기반 세대 배열" 섹션을 추가. 단, `clans.json` 의 `generations` 는 예시(미확정)이므로
   실제 본관 항렬은 종중 족보로 검증했음을 이용자에게 안내.
5. **문헌 보완** — `--lit <폴더>` 로 논문/족보 텍스트를 스캔, 공식 DB에 없는 이름을
   "문헌/논문에서 추가 발굴" 섹션에 표시. `--web` 은 `.env` 키 설정 시에만 동작.
6. **결과 해석** — 매칭 0명 ≠ "참여 안 함". 미신청 / 이명(異名) / 실명 미상 가능성 열림.
   반드시 가족(부모님) → 종중 족보 → 기념재단(063-530-9434) 교차검증 안내.

## Notes
- 공식 API 없음(일반 PHP 폼). GET 방법 A(`commit/?menu=231&sf=all&sv=<성씨>&pno=N`) 사용.
- DB는 성씨+이름+지역만 보유, 본관/파 필터 불가 → 정직한 후보 제시가 목적.
- `--pages` 상한을 걸어도 "처리 k/M — 남은 N페이지 있음"을 명시해 놓침 방지.
- 항렬 엔진: 이름 내 항렬 자 매칭 → 세대 배정. 데이터는 `clans.json` (족보 검증 필요).
- 문헌 모듈: 로컬 코퍼스(제로의존) 기본, 웹은 선택(env 키). 키 하드코딩 금지.
- 외부 의존성 0개 (Python 표준라이브러리만). `python3 -m venv .venv` 격리 권장.
