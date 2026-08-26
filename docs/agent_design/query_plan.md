# QueryPlan Draft

사용자 질문을 Agent가 어떤 작업으로 해석했는지 표현하는 구조입니다. 아래 필드는
[representative_questions.md](representative_questions.md)의 18문항에서 실제로 필요했던 것만
남긴 최소 집합이며, **확정 Schema가 아닌 Draft**입니다.

## 이 문서에서 쓰는 유형 번호

질문 유형 번호는 [representative_questions.md](representative_questions.md)를 따릅니다.

| 번호 | 유형 | 해당 문항 |
|---|---|---|
| 유형 1 | 단일 사실 검색 (Single Fact) | Q01~Q03 |
| 유형 2 | 단일 문서 요약 (Document Summary) | Q04~Q06 |
| 유형 3 | 시계열 비교 (Temporal Comparison) | Q07~Q09 |
| 유형 4 | 기업 간 비교 (Cross-entity Comparison) | Q10~Q12 |
| 유형 5 | 정정·후속 공시 추적 (Event History) | Q13~Q15 |
| 유형 6 | 답변 불가능 (Unanswerable) | Q16~Q18 |

## 문서군 표기 대응

`document_class`의 값과 한국어 표기의 대응은 다음과 같습니다.

```text
periodic → 정기공시     (사업·반기·분기보고서)
event    → 거래소공시   (공급계약 체결·해지, 신규시설투자 등)
holding  → 지분공시     (대량보유 상황 보고)
```

`event`는 구현 모듈명(`src/disclosure_agent/event`)과 맞춘 값이며, 한국어 표기는 repo 공통 문서를
따라 **거래소공시**로 통일합니다. 두 표기를 섞어 쓰지 않습니다.

## 설계 원칙

1. **QueryPlan은 Parser Schema가 아닙니다.** Parser는 원문에 무엇이 있는지를 표현하고,
   QueryPlan은 사용자가 무엇을 요청했는지를 표현합니다. 두 구조는 필드 이름이 겹치더라도 서로
   다른 계층입니다. `docs/data_contracts.md`의 Draft와 혼용하지 않습니다.
2. **문서군과 세부유형을 나눠 표현합니다.** 정기공시와 거래소공시는 세부유형 어휘가 서로
   배타적이라 하나의 필드로 표현하면 값 공간이 뒤섞입니다. `document_class`와 `document_subtype`
   으로 나눕니다.
3. **`accounting_scope`의 미지정은 의미 있는 상태입니다.** "연결"과 "지정하지 않음"을 구분하지
   못하면 사용자가 기준을 명시하지 않았을 때의 처리 정책을 검증할 수 없습니다. 기본값을 조용히
   채우지 않고 `unspecified`를 명시 상태로 유지합니다.
4. **기간은 회계기간과 공시일을 구분합니다.** 정기공시는 회계기간으로, 거래소·지분공시는 공시일로
   지정됩니다. 같은 "2025년"이라도 의미가 다르므로 종류 구분자를 함께 둡니다.
5. **`operations`는 QueryPlan이 요청하는 deterministic processing step의 목록입니다.**
   `AGENTS.md`가 계산을 Python으로 수행하도록 정하고 있으므로, 어떤 처리가 요청되었는지가
   QueryPlan에 드러나야 실행 경로를 검증할 수 있습니다. 이 목록은 **Calculation operation
   전용 목록이 아닙니다.** `compare`·`field_diff`·`state_resolution`은 Calculation을 만들지
   않습니다(→ 아래 표와 [agent_patterns.md](agent_patterns.md#c10-calculation의-범위)).
6. **`chain_policy`는 정정·후속 처리 방식을 표현합니다.** 같은 거래소공시라도 원본만 볼 것인지
   체인 전체를 볼 것인지에 따라 문서 선택 결과와 집계 결과가 달라집니다.

본 문서는 Pydantic 모델이나 함수 signature를 확정하지 않습니다. 필드 이름과 값 목록도 팀 검토
대상입니다.

## 핵심 필드

| # | 필드 | 의미 | 자료형 개념 | 예시 | 필요 유형 | 조건 |
|---|---|---|---|---|---|---|
| 1 | `intent` | 질문이 요구하는 작업 종류. 실행 Pattern을 결정 | 열거형 | `single_fact`, `document_summary`, `temporal_compare`, `cross_entity_compare`, `event_history`, `unanswerable_check` | 전 유형 | required |
| 2 | `companies` | 대상 기업. 사용자 표현과 해석된 법인명을 함께 보존 | 객체 배열 `{surface, resolved_name, role}` | `{surface: "한미반도체", resolved_name: "한미반도체", role: "subject"}` | 1~5 | 유형 4는 2개 이상 required |
| 3 | `periods` | 대상 기간. 회계기간과 공시일을 구분 | 객체 배열 `{kind, year, period_type, range}` | `{kind: "fiscal", year: 2025, period_type: "annual"}` / `{kind: "filing", range: "2025-09"}` | 전 유형 | 유형 3은 2개 required. 유형 1·2는 미지정 시 기본 정책 적용(D5-a Pending) |
| 4 | `document_class` | 어느 문서군을 볼 것인가 | 열거형 | `periodic`, `event`, `holding` | 전 유형 | required. Pattern과 Parser 경로를 가름 |
| 5 | `document_subtype` | 문서군 내 세부 종류 | 문자열 | `annual`, `quarter`, `half` / 공급계약 체결·해지, 신규시설투자 / 대량보유 보고 | 전 유형 | optional. 미지정 시 문서 목록 조회 단계에서 확정 |
| 6 | `accounting_scope` | 연결·별도 기준 | 열거형 | `consolidated`, `separate`, `unspecified` | 1·3·4 | 재무 수치 질문에서 사실상 required |
| 7 | `metrics` | 조회·계산 대상 지표. 사용자 표현과 해석된 개념을 함께 보존 | 객체 배열 `{surface, concept}` | `{surface: "매출", concept: "revenue"}` | 1·3·4 | 유형 3·4는 required |
| 8 | `operations` | 검색 이후 수행할 deterministic processing step 목록 (아래 구분표 참조) | 열거형 배열 | `none`, `delta`, `delta_pct`, `ratio`, `diff_pp`, `aggregate`, `compare`, `field_diff`, `state_resolution` | 3·4·5 | 유형 1·2·6은 `[none]` |
| 9 | `section_scope` | 문서 내 탐색 범위 힌트 | 열거형·문자열 | `financial_statement`, `business_overview`, `products`, `facility_investment`, `whole_document` | 2·3(Q09)·1 | 유형 2는 required |
| 10 | `chain_policy` | 정정·후속 문서 처리 방식 | 열거형 | `exclude_corrections`, `latest_only`, `full_chain` | 2(Q06)·4(Q12)·5 | 유형 5는 `full_chain` required |

`operations`의 값은 **Calculation을 만드는 것**과 **그렇지 않은 것**으로 갈립니다.

| 값 | 무엇을 만드는가 |
|---|---|
| `delta` `delta_pct` `ratio` `diff_pp` `aggregate` | **Numeric Calculation** — 수치형 파생 결과 |
| `compare` | **Comparison Judgment → ComparisonResult** — Calculation이 아님 |
| `field_diff` | field-level change resolution — Calculation이 아님 |
| `state_resolution` | latest-state resolution — Calculation이 아님 |

`compare`를 Calculation operation이라고 부르지 않습니다. 관계 판정의 결과는 별도의
ComparisonResult입니다(→
[agent_patterns.md](agent_patterns.md#comparison-judgment와-comparisonresult)).

`aggregate`는 Q12에서 기업별 투자금액 총합을 구하기 위해 필요한 연산입니다. 집계 이전에 동일 투자
건의 정정본을 하나로 합치는 처리가 선행되어야 하므로, Q12는 `chain_policy`도 함께 사용합니다.

## 보조 필드

특정 문항에서만 필요해 핵심 집합과 분리했습니다.

| 필드 | 의미 | 필요 문항 | 사유 |
|---|---|---|---|
| `entity_filters` | 계약상대방·보고자 등 본문 기반 식별 조건 `{counterparty, filer}` | Q02 Q03 Q12 Q14 | 메타데이터만으로 문서가 1건으로 좁혀지지 않는 경우가 실제로 존재 |
| `value_asof` | 한 표에 시점이 둘일 때 어느 값인지 (`prior`, `current`) | Q03 | 지분공시 요약 정보가 직전 보고 시점과 이번 보고 시점을 함께 담음 |

## 필드 선정 근거

- `report_type`과 `event_type`을 따로 두지 않고 `document_class` + `document_subtype` 두 필드로
  접었습니다. 두 어휘가 배타적이므로 한 축으로 표현하는 편이 라우팅에 그대로 쓰입니다.
- `comparison_mode`는 별도 필드로 두지 않았습니다. `intent`와 `operations`로 완전히 결정되며,
  중복 상태를 만들면 불일치 여지만 생깁니다.
- Answerability 상태는 QueryPlan에 두지 않습니다. 질문 해석 결과가 아니라 실행 결과이므로
  [evidence_and_answerability.md](evidence_and_answerability.md)에서 다룹니다.

## 문항별 QueryPlan 예시

값은 형태 예시이며 확정된 표기가 아닙니다.

**Q01 — 단일 사실 검색**

```text
intent            = single_fact
companies         = [한미반도체]
periods           = [{kind: fiscal, year: 2025, period_type: annual}]
document_class    = periodic
document_subtype  = annual
accounting_scope  = consolidated
metrics           = [{surface: "매출액", concept: revenue}]
operations        = [none]
section_scope     = financial_statement
chain_policy      = (판본 정책 — D5 Pending)
```

**Q10 — 기업 간 비교**

```text
intent            = cross_entity_compare
companies         = [HD현대중공업, 삼성중공업]
periods           = [{kind: fiscal, year: 2025, period_type: annual}]
document_class    = periodic
accounting_scope  = consolidated
metrics           = [{surface: "매출", concept: revenue}]
operations        = [compare]   # Calculation 0 → Comparison Judgment → ComparisonResult
section_scope     = financial_statement
```

**Q12 — 기업 간 비교 (이벤트 집계)**

```text
intent            = cross_entity_compare
companies         = [LG이노텍, 레인보우로보틱스]
periods           = [{kind: filing, year: 2024, note: "최초 공시 연도 기준"}]
document_class    = event
document_subtype  = 신규시설투자
metrics           = [{surface: "시설투자", concept: investment_amount}]
operations        = [aggregate, compare]   # aggregate → Numeric Calculation (기업별 합계 2건)
                                           # compare   → Comparison Judgment / ComparisonResult
chain_policy      = full_chain    # 정정 반영이 필요하되 중복 합산은 금지
```

**Q15 — 정정·후속 추적**

```text
intent            = event_history
companies         = [레인보우로보틱스]
periods           = [{kind: filing, range: 코퍼스 전 기간}]
document_class    = event
document_subtype  = 신규시설투자
operations        = [field_diff, state_resolution]
chain_policy      = full_chain
```

**Q18 — 답변 불가능**

```text
intent            = unanswerable_check
companies         = [한화오션]
document_class    = (미결정 — 문서 접근 이전 단계에서 종료)
operations        = [none]
```

## 미결정 사항

- `accounting_scope`가 `unspecified`일 때의 처리 정책(연결 기본값 적용 여부, 사용자 확인 요청
  여부)은 **Pending Team Decision**입니다.
- 기간이 지정되지 않은 질문(Q05)의 기본 문서 선택 정책은
  [open_decisions.md](open_decisions.md)의 D5-a에서 다룹니다.
- `chain_policy`의 기본값은 [open_decisions.md](open_decisions.md)의 D5에서 다룹니다.
- `metrics.concept`의 값 체계는 아직 정하지 않았습니다. 실제 데이터 사전 검토에서 정기공시
  재무제표 Section에 표준 계정 식별자로 보이는 속성이 관찰되었으나, 그 존재와 활용 범위는
  **Parser·구조 분석 결과와 교차 확인이 필요**하며 확정은 팀원 1의 분석 결과를 따릅니다.
- `operations`의 값 목록이 `docs/data_contracts.md` Draft의 계산 결과 표현과 같은 어휘를 쓸지는
  미정입니다([open_decisions.md](open_decisions.md) DC5 참조).
