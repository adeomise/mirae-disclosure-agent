# Coding Agent Rules

- 담당 영역과 파일을 불필요하게 수정하지 않는다.
- 이번 주 역할은 `docs/team_roles.md`를 따르며, 기능 모듈의 개인 소유권으로 해석하지 않는다.
- `src/disclosure_agent/periodic`, `event`, `retrieval`, `agent`, `api`는 향후 구현을 위한 공용 모듈이며 특정 팀원의 소유 폴더가 아니다.
- 이번 주에는 범용 Parser, 실제 Retrieval/LLM, API/HyperCLOVA X/검색 구현을 목표로 하지 않는다.
- 공통 Data Contract는 XML 분석, 질문 설계, 평가 요구사항을 반영해 확정할 Draft이며 팀 합의 없이 확정하거나 구현 계약으로 취급하지 않는다.
- 실제 공시 값이나 접수번호를 추정하지 않는다.
- 실제 원문 데이터와 Secret을 커밋하거나 코드에 작성하지 않는다.
- Parser는 원문의 값, 단위, 계층 구조를 보존한다.
- 계산은 Python 코드로 구현하며 LLM이 숫자를 직접 계산하게 하지 않는다.
- 모든 답변은 Evidence와 연결한다.
- 자유로운 Chain-of-Thought를 저장하거나 API 응답으로 반환하지 않는다.
- 구현 전 관련 문서와 Data Contract의 확정 여부를 확인한다.
- 작업 후 변경 파일과 실행한 테스트 결과를 보고한다.
