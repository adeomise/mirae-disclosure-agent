# 질문 유형·검색·Agent 동작 설계

팀원 2의 설계 산출물을 둡니다. 이 디렉터리의 목표는 질문에서 검색과 답변까지의 **동작 설계**이며,
실제 Retrieval이나 LLM 구현이 아닙니다.

다루는 범위는 `docs/team_roles.md`의 팀원 2 역할을 따릅니다.

- 지원할 질문 범위와 대표 질문
- 질문 유형 분류
- 질문별 필요한 문서와 정보
- 예상 검색 과정
- 이상적인 답변 형식
- 답변 가능한 범위와 불가능한 범위

## 문서 인덱스

| 문서 | 내용 |
|---|---|
| [representative_questions.md](representative_questions.md) | 대표 질문 18문항과 각 문항의 검증 목적, 난이도, Pattern, Parser 상태 |
| [query_plan.md](query_plan.md) | 사용자 질문을 Agent 작업으로 구조화하는 QueryPlan Draft |
| [agent_patterns.md](agent_patterns.md) | 6개 실행 Pattern과 필요한 Capability. **Capability 이름의 정본** |
| [retrieval_strategy.md](retrieval_strategy.md) | 메타데이터 필터에서 구조 탐색, 검색으로 이어지는 질문 유형별 전략 |
| [evidence_and_answerability.md](evidence_and_answerability.md) | Fact와 Evidence의 책임 구분, 답변 가능성 판정 체계 |
| [open_decisions.md](open_decisions.md) | 구현 전 팀 합의가 필요한 항목과 Data Contract Draft 차이 추적 |

## 공통 용어

이 디렉터리의 문서는 아래 표기를 일관되게 사용합니다.

| 표기 | 대상 | QueryPlan 값 |
|---|---|---|
| 정기공시 | 사업·반기·분기보고서 | `document_class=periodic` |
| **거래소공시** | 공급계약 체결·해지, 신규시설투자 등 | `document_class=event` |
| 지분공시 | 대량보유 상황 보고 | `document_class=holding` |

- 거래소공시는 repo 공통 문서 표기를 따른 한국어 정식 표기입니다. QueryPlan 값 `event`는 구현
  모듈명(`src/disclosure_agent/event`)과 맞춘 것으로, "이벤트공시"라는 별도 표기는 쓰지 않습니다.
- 문서 내부 구조는 **Section**으로 표기합니다. repo 공통 문서 표기를 따릅니다.
- **공시 코퍼스**는 `data/raw/`의 공시 원문 문서 집합을 가리키며, 평가용 Gold 자료를 뜻하는
  **평가 데이터셋**과 구분합니다.
- Capability 이름은 [agent_patterns.md](agent_patterns.md)의 표기를 정본으로 사용합니다.

## 현재 상태

- 문서는 모두 **초안**입니다. 팀 검토를 거쳐 확정합니다. 난이도, Capability의 MVP 우선순위, Evidence
  최소 건수는 모두 **팀원 2 권고값**이며 확정값이 아닙니다.
- 대표 질문은 **6유형 × 3문항 = 18문항** 기준입니다. `docs/team_roles.md`와
  `docs/development_plan.md`에는 15문항으로 표기되어 있으나, 두 문서는 공통 문서이므로 본 작업에서
  수정하지 않았습니다. 표기 정합은 팀 확인 사항입니다.
- 6문항은 거래소공시 Parser 결과에 의존합니다. 해당 문항은 `Event Parser Pending`으로 표시했으며
  삭제하거나 교체하지 않았습니다. 거래소공시 구조 분석과 Parser 설계는 팀원 1에게 별도로 요청된
  상태이며, 본 디렉터리에서 그 구조를 제안하지 않습니다.
- 문항 후보는 공시 코퍼스에서 답변 가능성을 확인한 뒤 선정했습니다. 다만 본 디렉터리의 문서에는
  실제 접수번호, 실제 수치, 원문 전문, 개인 식별정보를 기재하지 않습니다.
- 대표 질문에는 실제 기업명·연도·계약상대방·사업명이 사용됩니다. 실제 데이터 기반 설계의 핵심
  산출물이기 때문입니다. 다만 이 조합이 특정 공시를 식별할 수 있으므로, **사용 가능 범위는 저장소
  데이터 보안 정책과 함께 팀 확인이 필요합니다**([open_decisions.md](open_decisions.md) D6).

## 원문 구조에 관한 서술의 성격

본 디렉터리에서 문서 구조·표 배치·연결 정보에 관해 언급한 내용은 질문 후보를 검증하려고 **실제
데이터를 사전 검토하면서 관찰한 사항**입니다. 확정된 구조 분석 결과가 아니며,
`docs/data_analysis/`의 Parser·구조 분석 결과와 **교차 확인이 필요합니다**. 구조 분석과 Parser
설계의 확정은 팀원 1의 범위입니다.

## 다른 문서와의 관계

- `docs/data_contracts.md`는 Draft이며 본 설계 문서에서 수정하지 않았습니다. Agent 설계 요구와의
  차이는 [open_decisions.md](open_decisions.md)의 DC1~DC7에 추적 가능하게 기록했습니다.
- `docs/mvp_scope.md`, `docs/architecture.md`, `docs/evaluation/`의 내용은 복제하지 않고 참조만
  합니다.
- 평가 데이터셋 구조, 평가 기준, 평가 지표의 정의는 팀원 3의 `docs/evaluation/` 범위입니다. 본
  디렉터리는 요구사항과 제안만 제시합니다.
