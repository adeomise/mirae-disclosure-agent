# DART 공시 AI Agent

금융감독원 DART 공시 문서에서 근거를 찾고 비교·계산을 지원하는 AI Agent의 공동 개발
프로젝트입니다.

저장소 초기에는 개발 환경과 협업 구조만 마련한 scaffold 단계였습니다. XML Parser,
Retrieval/LLM, HyperCLOVA X 연동, 평가 자동화의 현재 구현 범위는 각 모듈 문서
(`docs/agent_design/`, `docs/data_analysis/`)를 따릅니다. FastAPI 애플리케이션과
`/api/ask`·`/health` 엔드포인트는 구현되어 있으며, 실행·배포 방법은 아래 `배포` 절과
`docs/deployment.md`를 따릅니다.

## MVP 목표

단일 기업의 정기공시 수치 조회와 기간·기업 비교, 간단한 비율 계산, 공급계약 및
신규시설투자 변경 추적, 답변 근거 문서·Section 제시가 가능한 1차 MVP를 목표로 합니다.
상세 범위와 일정은 이번 주 분석·설계 결과를 반영해 확정하며, 현재 기준은
`docs/mvp_scope.md`와 `docs/development_plan.md`를 따릅니다.

## 프로젝트 구조

- `src/disclosure_agent/common`: 공통 설정, 열거형, 예외, Schema 영역
- `src/disclosure_agent/periodic`: 정기공시 처리 영역
- `src/disclosure_agent/event`: 이벤트공시 처리 영역
- `src/disclosure_agent/retrieval`: 검색·결합 영역
- `src/disclosure_agent/agent`: 질의 분석, 계산, 안전성, 답변 생성 영역
- `src/disclosure_agent/api`: FastAPI 애플리케이션(`/api/ask`, `/health`) 영역
- `data`: 로컬 원문·가공 데이터·인덱스 위치
- `evaluation`: 평가 실행용 질문, 정답, 결과 위치
- `tests`: unit, integration, 비식별 fixture 위치
- `docs/data_analysis`: 공시 데이터·XML 구조 분석 문서
- `docs/agent_design`: 질문 유형·검색·Agent 동작 설계 문서
- `docs/evaluation`: 평가 데이터셋·평가 기준·아키텍처·프로젝트 관리 문서

`src/disclosure_agent/periodic`, `event`, `retrieval`, `agent`, `api`는 기능 영역별 모듈
구조입니다. 특정 팀원의 소유 폴더가 아니며, 팀 역할과 일대일로 대응하지 않습니다. 각 영역의
현재 구현 범위는 이 목록에서 단정하지 않으며 `docs/agent_design/`·`docs/data_analysis/`를
따릅니다.

## 이번 주 팀 역할

- 팀원 1: 공시 데이터·XML 구조 분석
- 팀원 2: 질문 유형·검색·Agent 동작 설계
- 팀원 3: 평가 데이터셋·아키텍처·프로젝트 관리

각 팀원의 상세 산출물과 협업 규칙은 `docs/team_roles.md`에서 확인합니다.

## Data Contract 상태

`docs/data_contracts.md`는 확정본이 아닌 **Draft**입니다. 팀원 1의 XML 분석, 팀원 2의 질문
설계, 팀원 3의 평가 요구사항을 함께 반영하고 팀 합의를 거친 뒤 확정합니다.

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

Windows에서는 `scripts/bootstrap.ps1`, `scripts/run_tests.ps1`, `scripts/run_api.ps1`을,
macOS/Linux에서는 대응하는 `.sh` 스크립트를 사용할 수 있습니다. `run_api.*`는
`uv run uvicorn disclosure_agent.api.main:app --host 0.0.0.0 --port 8000`을 실행합니다.

## 배포

Linux VM + Docker에서 서버를 띄우는 최소 절차(build/run, corpus volume mount, 환경 변수,
`/health`·`/api/ask` 확인)는 `docs/deployment.md`를 따릅니다.

## Branch 사용법

`develop`에서 작업 목적에 맞는 Branch를 만들고 Pull Request로 다시 `develop`에 병합합니다.

```bash
git switch develop
git switch -c docs/xml-analysis  # 또는 docs/agent-design, docs/evaluation-architecture
```

`main` 직접 Push는 금지합니다. 자세한 규칙은 `CONTRIBUTING.md`와
`docs/git_workflow.md`를 따릅니다.

## 데이터 배치 및 보안

- 실제 DART XML은 `data/raw/`에 로컬로만 둡니다.
- 가공 데이터는 `data/processed/`, Vector Index는 `data/indexes/`에 둡니다.
- 테스트에는 비식별·합성 fixture만 사용합니다.
- 실제 DART 원문, 기업의 실제 수치·접수번호, Secret은 GitHub에 올리지 않습니다.

## 구현 현황

FastAPI 애플리케이션과 `/api/ask`·`/health` 엔드포인트, 배포 골격(Dockerfile,
`docs/deployment.md`)은 구현되어 있습니다. 그 외 항목(XML Parser 커버리지, Keyword/Vector
Search 결합, 질의 분석·계산·근거 검증의 세부 범위, HyperCLOVA X 연동, 평가 자동화)의 현재
구현 범위는 이 섹션에서 단정하지 않으며 각 모듈 문서(`docs/agent_design/`,
`docs/data_analysis/`, `evaluation/README.md`)를 따릅니다.
