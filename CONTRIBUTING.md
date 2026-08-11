# Contributing

## Branch

- `main`: 배포 가능한 기준선. 직접 Push 금지
- `develop`: 변경 통합 Branch
- `docs/xml-analysis`: 공시 데이터·XML 구조 분석 작업 예시
- `docs/agent-design`: 질문 유형·검색·Agent 동작 설계 작업 예시
- `docs/evaluation-architecture`: 평가 데이터셋·아키텍처·프로젝트 관리 작업 예시

작업자는 목적에 맞는 Feature 또는 Docs Branch에서 작업하고 Pull Request를 통해 `develop`에
병합합니다. 브랜치 이름은 작업 목적을 나타내며 소유권을 부여하지 않습니다.

## 이번 주 기여 범위

- 팀원 1의 분석 문서는 `docs/data_analysis/`에 둡니다.
- 팀원 2의 설계 문서는 `docs/agent_design/`에 둡니다.
- 팀원 3의 평가·아키텍처 문서는 `docs/evaluation/`과 관련 공통 문서에 둡니다.
- 상세 역할과 산출물은 `docs/team_roles.md`를 따릅니다.

기존 `src/disclosure_agent/periodic`, `event`, `retrieval`, `agent`, `api`는 향후 시스템 구현을
위한 공용 모듈입니다. 특정 팀원의 소유 폴더가 아니므로 이번 주 역할에 따라 임의로 삭제,
이동, 변경하지 않습니다.

## Data Contract 변경

`docs/data_contracts.md`는 확정본이 아닌 Draft입니다. XML 분석, 질문·Agent 설계, 평가
요구사항을 반영하고 팀 합의를 거친 뒤 확정합니다. Draft 변경 PR에는 다음 내용을 기록합니다.

- 어떤 분석 또는 요구사항이 변경의 근거인지
- 원문 값·단위·계층 구조 보존에 미치는 영향
- 질문 처리와 Gold 데이터셋·평가에 미치는 영향
- 하위 호환성 또는 기존 예시 변경 여부

큰 작업은 먼저 Issue를 생성합니다. 실제 공시 원문, 실제 기업 수치·접수번호, API Key 등
Secret은 커밋하지 않습니다. PR에는 작업 내용, 검증 여부, 영향 범위를 기록합니다.

## Commit 예시

- `docs: analyze disclosure XML structures`
- `docs: define representative question types`
- `docs: draft gold dataset schema`
- `docs: revise data contract from analysis`
- `fix: preserve financial statement unit`

## Pull Request 전 확인

문서 변경은 링크, 용어, 역할 범위의 일관성을 확인합니다. 코드가 포함된 변경은 아래 검사도
실행합니다.

```bash
uv run ruff check .
uv run pytest
```
