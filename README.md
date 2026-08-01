# DART 공시 AI Agent

금융감독원 DART 공시 문서에서 근거를 찾고 비교·계산을 지원하는 AI Agent의 공동 개발
프로젝트입니다. 공모전 과제로 3명이 정기공시, 이벤트공시, 공통 런타임 영역을 나누어
개발합니다.

현재 저장소는 **개발 환경과 협업 구조만 마련한 scaffold 단계**입니다. XML 파싱, 검색,
Vector Search, 계산, HyperCLOVA X 연동, FastAPI 엔드포인트와 평가는 아직 구현되지 않았으며
실행 가능한 API도 제공하지 않습니다.

## MVP 목표

2026년 8월 11일까지 단일 기업의 정기공시 수치 조회와 기간·기업 비교, 간단한 비율 계산,
공급계약 및 신규시설투자 변경 추적, 답변 근거 문서·Section 제시가 가능한 1차 MVP를
완성합니다. 상세 범위는 `docs/mvp_scope.md`를 따릅니다.

## 프로젝트 구조

- `src/disclosure_agent/common`: 공통 설정, 열거형, 예외, Schema 예정 영역
- `src/disclosure_agent/periodic`: 정기공시 처리 예정 영역
- `src/disclosure_agent/event`: 이벤트공시 처리 예정 영역
- `src/disclosure_agent/retrieval`: 검색·결합 예정 영역
- `src/disclosure_agent/agent`: 질의 분석, 계산, 안전성, 답변 생성 예정 영역
- `src/disclosure_agent/api`: FastAPI 예정 영역
- `data`: 로컬 원문·가공 데이터·인덱스 위치
- `evaluation`: 평가 질문, 정답, 결과 위치
- `tests`: unit, integration, 비식별 fixture 위치
- `docs`: 아키텍처와 협업 계약 문서

## 팀 역할

- 담당자 1: 정기공시 XML, 재무제표·주석 추출, Chunk 생성
- 담당자 2: 이벤트공시 XML, 원본·정정공시 연결과 변경 비교
- 담당자 3: 공통 Schema, 검색·Agent 런타임, HyperCLOVA X, API, 평가 자동화

세부 소유권과 공동 작업 규칙은 `docs/team_roles.md`에서 확인합니다.

## 개발 환경 설치

Python 3.12와 [uv](https://docs.astral.sh/uv/)가 필요합니다.

```bash
uv sync
```

환경 변수는 `.env.example`을 참고하되 실제 키가 포함된 `.env`는 커밋하지 않습니다.

## 개발 명령

```bash
uv run ruff check .
uv run pytest
```

Windows에서는 `scripts/bootstrap.ps1`, `scripts/run_tests.ps1`을, macOS/Linux에서는 대응하는
`.sh` 스크립트를 사용할 수 있습니다. API 실행 스크립트는 현재 미구현 안내만 출력합니다.

## Branch 사용법

`develop`에서 자신의 Branch를 만들고 Pull Request로 다시 `develop`에 병합합니다.

```bash
git switch develop
git switch -c feat/periodic-slice  # 또는 feat/event-slice, feat/runtime-api
```

`main` 직접 Push는 금지합니다. 자세한 규칙은 `CONTRIBUTING.md`와
`docs/git_workflow.md`를 따릅니다.

## 데이터 배치 및 보안

- 실제 DART XML은 `data/raw/`에 로컬로만 둡니다.
- 가공 데이터는 `data/processed/`, Vector Index는 `data/indexes/`에 둡니다.
- 테스트에는 비식별·합성 fixture만 사용합니다.
- 실제 DART 원문, 기업의 실제 수치·접수번호, Secret은 GitHub에 올리지 않습니다.

## 아직 구현되지 않은 기능

- 정기·이벤트공시 XML Parser와 추출·변경 비교
- Keyword/Vector Search 및 검색 결과 결합
- 질의 분석, 라우팅, 계산, 근거 검증과 답변 생성
- HyperCLOVA X 연동
- FastAPI 애플리케이션과 엔드포인트
- 평가 실행 및 자동화

개발 순서는 Pilot XML 선정 → 공통 Schema 확정 → 담당 영역 구현 → 1차 통합 → 오류 수정 →
MVP 완료 순입니다.
