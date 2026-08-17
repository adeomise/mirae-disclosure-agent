# Agent Pattern과 Required Capability

18개 대표 질문에서 도출한 실행 흐름 6개와, 그 흐름을 수행하기 위해 Agent에게 필요한 최소
Capability를 정리합니다. 본 문서는 동작 설계이며 실제 구현이나 API 확정이 아닙니다.

문서군 표기는 [query_plan.md](query_plan.md)의 대응(`periodic`=정기공시, `event`=거래소공시,
`holding`=지분공시)을 따릅니다. 문서 내부 구조는 **Section**으로 통일해 표기합니다.

> **원문 구조에 관한 서술의 성격** — 이 문서에서 원문 구조·연결 정보에 관해 언급한 내용은 질문
> 후보를 검증하려고 **실제 데이터를 사전 검토하면서 관찰한 사항**입니다. 확정된 구조 분석 결과가
> 아니며, `docs/data_analysis/`의 Parser·구조 분석 결과와 **교차 확인이 필요합니다**.

## Tool이 아니라 Capability로 설계하는 이유

주최 측 설명회 참고 자료에는 회사 식별, 공시 목록 조회, Section 목록·읽기, Chunk 검색, 이벤트 조회,
History 조회에 해당하는 Tool 예시가 제시되었습니다. 본 프로젝트는 그 **Tool 이름과 개수를 그대로
채택하지 않고, 18문항을 해결하는 데 실제로 필요한 동작 단위(Capability)로 설계**합니다.

이유는 두 가지입니다. 첫째, Tool 경계는 구현 시점의 호출 편의에 좌우되지만 Capability 경계는 질문이
요구하는 능력에서 나오므로 평가 항목과 직접 연결됩니다. 둘째, 실제 데이터 사전 검토에서 참고
자료에 없던 동작 두 가지(문서 확정, 단위·개념 정규화)의 필요성이 관찰되었기 때문입니다.

Tool API 이름과 signature는 이 문서에서 확정하지 않습니다. **Capability 이름은 본 저장소의
Agent 설계 문서 전체에서 정본 표기로 사용합니다.**

## Pattern 개요

| Pattern | 적용 문항 | 핵심 성격 |
|---|---|---|
| A. Single Fact Lookup | Q01 Q02 Q03 | 문서 1건 → 값 1개 |
| B. Section Summary | Q04 Q05 Q06 | 문서 1건 → 지정 범위 요약 |
| C. Temporal Comparison | Q07 Q08 Q09 | 같은 기업 → 기간 2개 |
| D. Cross-entity Comparison | Q10 Q11 Q12 | 기업 2개 → 같은 기간. Q12는 이벤트 집계 포함 |
| E. Event History | Q13 Q14 Q15 | 문서 체인 → 변경 이력·최종 상태 |
| F. Answerability Gate | Q16 Q17 Q18 | 거부 판정. A~E의 선행·후행 관문 |

18문항은 각각 정확히 하나의 Pattern에 배정됩니다. Pattern F는 독립 유형인 동시에 다른 Pattern의
실행 중에도 작동합니다. 예를 들어 Q14에서 원공시 시점의 금액은 공시유보 상태이므로, Pattern E
실행 중에 Pattern F 판정으로 부분 거부되어야 합니다.

각 Pattern의 필요 Capability는 **연쇄 참조 없이 전부 나열**합니다. Pattern 한 절만 읽어도 필요한
Capability가 완결되도록 했습니다.

## Pattern A — Single Fact Lookup

```text
Query Analysis
→ Answerability Gate (선행 판정)
→ Company Resolution
→ Filing Discovery
→ Document Selection (후보 복수 시 Document Disambiguation)
→ Section Navigation
→ Fact Retrieval
→ Validation
→ Evidence Verification
```

- **적용 문항** — Q01, Q02, Q03
- **입력 특징** — `intent=single_fact`, 기업 1개, 기간 1개, `operations=[none]`
- **필요 Capability**
  - C1 Company Resolution — Q01 Q02 Q03
  - C2 Filing Discovery — Q01 Q02 Q03
  - C3 Document Disambiguation — Q02
  - C4 Section Navigation — Q01 Q03
  - C5 Fact Retrieval — Q01 Q02 Q03
  - C6 Event Retrieval — Q02
  - C11 Evidence Verification — Q01 Q02 Q03
  - C12 Answerability Judgment — Q01 Q02 Q03 (유보·미기재 판정 포함)
  - C15 PII Filtering — Q03
- **검증 조건**
  1. 문서가 1건으로 좁혀졌는가
  2. Fact의 기준이 요청한 `accounting_scope`와 일치하는가
  3. 값과 단위가 같은 출처에서 함께 나왔는가
  4. 값이 공시유보·미기재 상태는 아닌가 (C12)
  5. Q03의 경우 요청한 시점(`value_asof`)의 값을 취했는가
- **출력 요구사항** — 값, 단위, 기간, 기준을 함께 제시. Evidence 1건 이상
- **주요 실패 모드** — 같은 Section에 있는 다른 기준의 표에서 값을 취함, 문서 후보를 좁히지 못한 채
  첫 번째 문서를 선택, 단위를 다른 Section에서 빌려옴, 직전·이번 시점 값을 잘못 선택

## Pattern B — Section Summary

```text
Query Analysis
→ Answerability Gate (선행 판정)
→ Company Resolution
→ Filing Discovery (기간 미지정 시 기본 정책 적용 — D5-a Pending)
→ Document Selection (판본 정책 적용 — D5 Pending)
→ 범위 선택
     ├ 정기공시: Section Navigation (Section 목록 확인 → 지정 범위)
     └ 거래소공시: Event Retrieval (번호 필드 구조. Section 전제 없음)
→ 범위 내 본문·표 읽기
→ 항목 단위 요약
→ 항목별 Evidence Verification
```

- **적용 문항** — Q04, Q05, Q06
- **입력 특징** — `intent=document_summary`, `section_scope` 필수(정기공시), `operations=[none]`
- **필요 Capability**
  - C1 Company Resolution — Q04 Q05 Q06
  - C2 Filing Discovery — Q04 Q05 Q06 (Q05의 기본 문서 선택이 여기에 해당)
  - C4 Section Navigation — Q04 Q05
  - C6 Event Retrieval — Q06
  - C11 Evidence Verification — Q04 Q05 Q06
  - C12 Answerability Judgment — Q04 Q05 Q06
  - C13 Semantic Search — Q04 (Section 경로 매핑 실패 시 조건부 fallback)
- **검증 조건**
  1. 요약 범위가 지정한 범위를 벗어나지 않았는가
  2. 요약의 각 항목이 개별 Evidence를 갖는가
  3. 기간 미지정 시 일관된 기본 문서 선택 정책이 적용되었는가 (Q05)
  4. 판본 선택 정책이 일관되게 적용되었는가 (Q06)
- **출력 요구사항** — 항목별 요약과 항목마다의 Evidence. 원문에 기재된 비율·구성비는 인용하고
  재계산하지 않습니다
- **주요 실패 모드** — 지정 범위를 벗어난 Section까지 요약에 포함, 문서 전체 요약으로 확산,
  원문에 있는 값을 다시 계산해 원문과 어긋난 값 제시, 원본과 정정본을 섞어 요약

## Pattern C — Temporal Comparison

수치 비교와 서술 비교가 갈리므로 흐름을 분기합니다. Q09는 계산 경로를 지나지 않습니다.

```text
Query Analysis
→ Answerability Gate (선행 판정)
→ Company Resolution
→ Filing Discovery (기간별 각각)
→ Document Selection ×2
→ Section Navigation (양쪽 동일 경로)
→ Content Extraction
     ├ 수치 경로 (Q07 Q08)
     │    → Fact Retrieval ×2
     │    → Unit & Concept Normalization
     │    → Comparability Validation
     │    → Calculation
     └ 서술 경로 (Q09)
          → 본문 추출 ×2
          → Narrative Diff
→ Evidence Verification (기간별 각각)
```

- **적용 문항** — Q07, Q08, Q09
- **입력 특징** — `intent=temporal_compare`, 기업 1개, 기간 2개
- **필요 Capability**
  - C1 Company Resolution — Q07 Q08 Q09
  - C2 Filing Discovery — Q07 Q08 Q09
  - C4 Section Navigation — Q07 Q08 Q09
  - C5 Fact Retrieval — Q07 Q08
  - C8 Unit & Concept Normalization — Q07 Q08
  - C9 Comparability Validation — Q07 Q08
  - C10 Calculation — Q07 Q08
  - C11 Evidence Verification — Q07 Q08 Q09
  - C12 Answerability Judgment — Q07 Q08 Q09
  - C14 Narrative Diff — Q09
- **검증 조건** — 수치 경로의 비교 가능성 4항목
  1. 동일 기업
  2. 동일 지표 개념
  3. 동일 `accounting_scope`
  4. 단위 일치 또는 정규화 성공

  하나라도 실패하면 계산을 수행하지 않습니다. 서술 경로(Q09)는 1·3항만 적용합니다.
- **출력 요구사항** — 두 기간 값, 계산 결과, 계산식. Evidence는 기간별로 각각 유지합니다.
  **근거 문서 건수 요건은 D4 Pending**이며 현재 Draft 권고값은
  [evidence_and_answerability.md](evidence_and_answerability.md)를 따릅니다.
- **주요 실패 모드** — 기간이 둘이라는 이유만으로 비교 수행, 분자와 분모를 다른 표에서 취해 기준
  불일치, 한쪽 기간 문서가 없을 때 다른 연도로 임의 대체, 서술 질문에서 억지 수치화

## Pattern D — Cross-entity Comparison

축이 기간이 아니라 기업입니다. Q12는 기업별로 **여러 이벤트를 모아 합산**하는 단계가 추가됩니다.

```text
Query Analysis
→ Answerability Gate (선행 판정)
→ Company Resolution ×2
→ 기업별 자료 확보 (각각 독립 수행)
     ├ 정기공시 경로 (Q10 Q11)
     │    → Filing Discovery → Document Selection
     │    → Section Navigation → Fact Retrieval
     └ 거래소공시 집계 경로 (Q12)
          → Event Retrieval (대상 연도 최초 공시 건 집합 확정)
          → Document Disambiguation (동일 투자 건 판별)
          → Event History Resolution (건별 최신 정정값 반영, 중복 합산 금지)
          → Fact Retrieval (건별 금액)
          → Aggregation (기업별 합계)
→ Unit & Concept Normalization
→ Comparability Validation
→ Calculation (비교)
→ Evidence Verification (기업별 각각)
```

- **적용 문항** — Q10, Q11, Q12
- **입력 특징** — `intent=cross_entity_compare`, 기업 2개, 기간 1개.
  Q12는 `operations=[aggregate, compare]`와 `chain_policy` 사용
- **필요 Capability**
  - C1 Company Resolution — Q10 Q11 Q12
  - C2 Filing Discovery — Q10 Q11 Q12
  - C3 Document Disambiguation — Q12
  - C4 Section Navigation — Q10 Q11
  - C5 Fact Retrieval — Q10 Q11 Q12
  - C6 Event Retrieval — Q12
  - C7 Event History Resolution — Q12
  - C8 Unit & Concept Normalization — Q10 Q12
  - C9 Comparability Validation — Q10 Q11 Q12
  - C10 Calculation — Q10 Q11 Q12
  - C11 Evidence Verification — Q10 Q11 Q12
  - C12 Answerability Judgment — Q10 Q11 Q12
- **검증 조건** — Pattern C 수치 경로의 4항목에 더해
  5. 결산월 일치 (현재 코퍼스 관찰 기준이며 일반 규칙으로 확정하지 않음)
  6. 계정 체계 호환. 한쪽 기업에 해당 지표 개념이 존재하지 않으면 비교 불가
  7. 파생지표 비교 시 분모 개념 동일 여부 (Q11)
  8. **집계 대상 집합의 정확성** (Q12) — 대상 연도에 최초 공시된 건만 포함했는가, 동일 투자 건의
     정정본을 별건으로 세지 않았는가, 각 건에 필드 단위 최신값을 반영했는가
- **출력 요구사항** — 기업별 값을 **원문 단위와 정규화 단위 모두 병기**. 정규화 결과만 제시하면
  사용자가 검증할 수 없습니다. Q12는 합계뿐 아니라 **합계에 포함된 투자 건 목록**을 함께 제시해야
  중복·누락을 확인할 수 있습니다. Evidence는 기업별로 각각 유지합니다
- **주요 실패 모드** — 단위 표기 차이를 놓쳐 자릿수 오답, 지표가 없는 기업에 대해 유사 계정으로
  대체, 비교 결과를 우열 판단이나 투자 의견으로 확대, **동일 투자 건의 원공시와 정정공시를 중복
  합산하거나 최신 정정값을 반영하지 않아 기업별 총액을 잘못 계산**

## Pattern E — Event History

다른 Pattern과 근본적으로 다릅니다. 문서 간 이동이 검색이 아니라 **문서에 기재된 연결 정보**를
따라가는 방식입니다.

```text
Query Analysis
→ Answerability Gate (선행 판정)
→ Company Resolution
→ Event Retrieval
→ 기준 문서 선택 (후보 복수 시 Document Disambiguation)
→ Event History Resolution
     ├ 역방향: 정정공시 → 대상 원공시
     └ 후속방향: 원공시 → 후속·해지 공시
   (재귀 탐색, 루트 도달까지)
→ Chain Completeness Validation
→ 필드 단위 변경점 비교
→ 공시일 기준 시간순 정렬
→ 최종 상태 판정
→ Evidence Verification (체인 전 문서)
```

- **적용 문항** — Q13, Q14, Q15
- **입력 특징** — `intent=event_history`, `document_class=event`, `chain_policy=full_chain`
- **필요 Capability**
  - C1 Company Resolution — Q13 Q14 Q15
  - C2 Filing Discovery — Q13 Q14 Q15
  - C3 Document Disambiguation — Q14
  - C6 Event Retrieval — Q13 Q14 Q15
  - C7 Event History Resolution — Q13 Q14 Q15
  - C11 Evidence Verification — Q13 Q14 Q15
  - C12 Answerability Judgment — Q13 Q14 Q15 (유보·체인 미완결·연결 모호 판정)
- **검증 조건**
  1. **체인 완결성** — 루트 원공시가 코퍼스 안에 존재하는가
  2. **연결 확정성** — 연결 대상이 1건으로 특정되는가. 원문이 접수번호가 아닌 날짜와 서류명을
     제공하는 것으로 관찰되므로, 같은 날짜에 후보가 복수일 수 있습니다
  3. 공시일 순서에 모순이 없는가
  4. 원문 정정 표의 값과 본문 값이 일치하는가
- **출력 요구사항** — 시간순 이력, 필드별 변경 궤적, 최종 상태, 체인 전 문서의 Evidence.
  체인이 완결되지 않으면 부분 이력임을 명시합니다
- **주요 실패 모드** — 정정공시만 읽고 원공시를 연결하지 못함, 연결 대상이 모호한데 임의 선택,
  이력 일부만 제시하고 완결된 것처럼 답변, 코퍼스 밖 원공시의 내용을 추정

## Pattern F — Answerability Gate

```text
Query Analysis
→ 정책 판정            (위반 시 종료. 문서 접근 없음)
→ 데이터군 판정         (요구 정보 유형이 보유 문서군에 있는가)
→ 범위 판정            (보유 기간·커버리지와 대조)
→ [문서 조회]          (여기까지 통과한 경우에만)
→ 거부 또는 계속 진행
```

- **적용 문항** — Q16, Q17, Q18. 그리고 A~E 전 Pattern의 선행 관문
- **입력 특징** — `intent=unanswerable_check`로 확정되거나, 다른 Pattern 실행 중 조기 종료
- **필요 Capability**
  - C12 Answerability Judgment — Q16 Q17 Q18
  - C1 Company Resolution — Q16 Q18
  - C2 Filing Discovery — Q16 (결과가 비어 있음을 확인하는 용도)
- **검증 조건** — 정책 위반 여부, 요구 데이터군 존재 여부, 요구 기간의 보유 범위 포함 여부
- **출력 요구사항** — 거부와 **사유 코드**, 보유 범위 안내. 근거 없는 대체 추정을 하지 않습니다.
  거부 응답에는 Evidence를 붙이지 않습니다
- **주요 실패 모드** — 유사한 다른 기간·다른 지표로 대체 답변, 공시 본문을 근거로 시장 반응이나
  전망을 추정, 지표를 제시하면서 사실상 투자 의견 제공

## Required Capability

`Proposed MVP Priority`는 **팀원 2의 권고안**이며 팀 확정값이 아닙니다. MVP 범위는
`docs/mvp_scope.md`를 따르며 최종 조정은 팀원 3과 함께 합니다.

`관련 모듈`은 현재 scaffold의 모듈 설명을 근거로 한 **구현 위치 후보**이며 담당 배정을 뜻하지
않습니다. `docs/team_roles.md`에 따라 해당 디렉터리는 공용 모듈입니다.

| # | Capability | 역할 | 사용 문항 | 관련 모듈 | Proposed MVP Priority |
|---|---|---|---|---|---|
| C1 | Company Resolution | 사용자 기업명 표현을 공시 코퍼스의 법인명으로 해석. 통용명과 법인명이 다른 경우와 문자 정규화 방식 차이를 흡수 | Q01~Q16, Q18 | `retrieval/metadata_filter` | MUST |
| C2 | Filing Discovery | 기업·기간·문서군·정정 여부로 후보 공시 목록 조회. 기간 미지정 시 기본 문서 선택 포함 | Q01~Q16 | `retrieval/metadata_filter` | MUST |
| C3 | **Document Disambiguation** | 후보가 복수일 때 본문 필드로 1건 확정 | Q02 Q12 Q14 | `retrieval/service` 경계 | MUST |
| C4 | Section Navigation | 문서의 Section 목록 확인과 지정 Section 읽기 | Q01 Q03 Q04 Q05 Q07~Q11 | `periodic/parser` 출력, `periodic/chunker` | MUST |
| C5 | Fact Retrieval | Section·Table·필드에서 값·단위·기간·기준을 갖춘 Fact 확보 | Q01 Q02 Q03 Q07 Q08 Q10 Q11 Q12 | `periodic/extractor` | MUST |
| C6 | Event Retrieval | 기업·이벤트 유형·기간으로 거래소공시 조회 및 대상 집합 확정. 본문 필드 조건이 필요해 C2와 분리 | Q02 Q06 Q12~Q15 | `event/service` | MUST |
| C7 | **Event History Resolution** | 원공시·정정·후속 체인의 양방향 재귀 복원, 완결성 판정, 필드 단위 최신값 확정 | Q12~Q15 | `event/linker`, `event/diff` | MUST |
| C8 | **Unit & Concept Normalization** | 금액 단위 정규화, 지표 개념 통합, 괄호 음수·구분 기호 처리 | Q07 Q08 Q10 Q12 | 구현 위치 **미정** ([D2](open_decisions.md)) | MUST |
| C9 | **Comparability Validation** | 비교 수행 전 기준·단위·개념·결산월·계정 체계 정합 판정 | Q07 Q08 Q10~Q12 | `agent/calculator` 진입 게이트 | MUST |
| C10 | Calculation | Fact 배열에서 결정적 결과 산출(증감·비율·집계·비교). LLM이 수치를 직접 계산하지 않음 | Q07 Q08 Q10~Q12 | `agent/calculator` | MUST |
| C11 | **Evidence Verification** | Fact·계산 결과·주장을 원문 위치에 연결하고 일치를 검증 | Q01~Q15 | `agent/evidence` | MUST |
| C12 | **Answerability Judgment** | 지원 가능 여부와 사유 코드 판정. 전 Pattern의 선행 관문 | 전 문항 | `agent/safety`, `agent/evidence` | MUST |
| C13 | Semantic Search | Section 경로 매핑 실패 시의 조건부 fallback | Q04 | `retrieval/keyword`, `vector`, `fusion` | SHOULD |
| C14 | Narrative Diff | 서술 텍스트 2건에서 변화 항목 도출 | Q09 | 구현 위치 미정 | MUST |
| C15 | PII Filtering | 지분공시에 포함될 수 있는 개인 식별정보 처리 | Q03 | `agent/safety` | **팀 확인 필요** |

- **C14를 MUST로 둔 이유** — Q09의 Primary Capability이며, 대표 질문 18문항은 모두 필수이므로
  선택 등급과 모순이 생기지 않도록 했습니다.
- **C15를 등급 대신 "팀 확인 필요"로 둔 이유** — Gold·Evidence 작성 시 개인 식별정보가 포함되지
  않아야 한다는 점은 분명하지만, Agent 응답 단계의 처리 수준은 팀 정책 확인 대상입니다. 이 문서에서
  구현 정책을 확정하지 않습니다([open_decisions.md](open_decisions.md) D6).
- **C13을 Q04에만 배정한 이유** — Q05는 문서 선택 문제이지 검색 문제가 아니고, Q09는 Section 경로로
  위치를 지정할 수 있어 의미 검색이 필요하지 않다고 판단했습니다.

## 실제 데이터 사전 검토에서 필요성이 관찰된 Capability

아래 항목은 참고 자료의 Tool 목록만으로는 드러나지 않았고, 질문 후보를 검증하는 과정에서
필요성이 관찰되었습니다. **Parser·구조 분석 결과와 교차 확인이 필요합니다.**

- **C3 Document Disambiguation** — 같은 기업이 같은 날짜에 같은 상대방과의 계약 공시를 복수 제출한
  사례가 관찰되었습니다. 메타데이터만으로는 1건으로 좁혀지지 않습니다.
- **C6 Event Retrieval의 집합 확정 역할** — Q12처럼 여러 이벤트를 모을 때, 대상 연도에 접수된
  문서만 모으면 이후 연도의 정정이 누락되고, 정정본을 그대로 포함하면 같은 투자 건이 중복됩니다.
  조회 자체가 집합 정의 문제입니다.
- **C7 Event History Resolution** — 원문의 연결 정보가 접수번호가 아닌 날짜와 서류명으로 보이며,
  정정이 직전 정정을 가리키는 다단 체인과 루트가 코퍼스 밖인 체인이 함께 관찰되었습니다. 또한 가장
  최근 정정본이 특정 필드를 바꾸지 않는 경우가 있어 필드 단위 최신값 추적이 필요합니다.
- **C8 Unit & Concept Normalization** — 같은 업종·같은 기간·같은 표준 계정으로 보이는데 두 기업의
  금액 단위 표기가 다른 사례가 관찰되었습니다. 정규화를 누락하면 자릿수 오답이 조용히 발생합니다.
  한 문서 안에서도 Section에 따라 같은 지표의 단위 표기가 다를 수 있습니다.
- **C9 Comparability Validation** — 업종에 따라 손익 계정 체계가 달라 특정 지표가 아예 존재하지 않는
  기업이 관찰되었습니다. 비교를 시도하기 전에 판정이 필요합니다.
- **C11 Evidence Verification** — `AGENTS.md`가 모든 답변을 Evidence와 연결하도록 정하고 있습니다.
- **C12 Answerability Judgment** — 검색 실패와 데이터 부재를 구분해야 합니다.
  [evidence_and_answerability.md](evidence_and_answerability.md)를 따릅니다.

## 미결정 사항

- Section 목록 조회에 해당하는 동작을 별도 Capability로 노출할지, Parser 산출물에 흡수할지는
  **Pending Team Decision**입니다. 현재는 C4에 포함해 두었습니다.
- C8의 구현 위치는 [open_decisions.md](open_decisions.md)의 D2에서 다룹니다.
- C14의 구현 위치는 미정입니다.
- Pattern C·D의 근거 문서 건수 요건은 [open_decisions.md](open_decisions.md)의 D4에 따릅니다.
- `Proposed MVP Priority` 전체가 팀 확정 대상입니다.
