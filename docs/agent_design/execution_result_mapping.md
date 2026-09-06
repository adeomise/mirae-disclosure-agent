# Pattern 결과 → AgentExecutionResult 대응표

Pattern A(단일 사실)와 Pattern E(정정 이력)의 내부 결과를 공통 실행 Contract인
`common.schemas.AgentExecutionResult`로 옮기는 규칙을 정리합니다. 구현은
`src/disclosure_agent/agent/normalization.py` 한 곳이며, 이 문서는 그 대응표의 근거를 남깁니다.

`disclosure_agent.agent.router.answer_question`은 Pattern에 관계없이
`AgentExecutionResult` 하나를 돌려줍니다.

## 1. 두 상태를 나누는 기준

`execution_status`와 `answerability_status`는 서로 다른 질문에 답합니다.

| 필드 | 질문 |
|---|---|
| `execution_status` | 실행이 정상적으로 끝났는가 |
| `answerability_status` | 확보한 근거로 답할 수 있는가 |

그래서 "파이프라인이 멈췄다"는 사실 하나만으로 `execution_status`를 정하지 않습니다.
판정 기준은 하나입니다.

- **코퍼스를 볼 만큼 보고 내린 판정인가** → `COMPLETED` + 답변 가능성 판정
- **그 판정에 이르지 못하고 멈춘 것인가** → `BLOCKED` + `ExecutionIssue`

`BLOCKED`에서는 `answerability_status`를 비웁니다. 답할 수 있는지 없는지를 확인하지
못했기 때문입니다. `UNANSWERABLE`로 채우면 "코퍼스에 답이 없다"는 확정 진술이 되어
검색 실패와 파이프라인 고장이 한 지표로 묶입니다.

## 2. Pattern A — 단일 사실

`COMPLETED` 판정에는 `answerability_detail`(stage/reason/detail 원문)이 함께 실립니다.

| stage / reason | execution_status | answerability_status | answerability_reason | execution_issue |
|---|---|---|---|---|
| (성공) | `COMPLETED` | `ANSWERABLE` | – | – |
| `filing_discovery` / `no_matching_filing` | `COMPLETED` | `UNANSWERABLE` | `NOT_FOUND` | – |
| `extraction` / `no_metric_acode` | `COMPLETED` | `UNANSWERABLE` | `INSUFFICIENT_EVIDENCE` | – |
| `extraction` / `no_matching_context` | `COMPLETED` | `UNANSWERABLE` | `INSUFFICIENT_EVIDENCE` | – |
| `extraction` / `ambiguous_candidates` | `COMPLETED` | `UNRESOLVED` | `AMBIGUOUS_MATCH` (§4 참조) | – |
| `parsing` / `parse_failed` | `BLOCKED` | – | – | 있음 |
| `extraction` / `missing_acontext` | `BLOCKED` | – | – | 있음 |
| `extraction` / `missing_table_unit` | `BLOCKED` | – | – | 있음 |
| `extraction` / `untrusted_table_unit` | `BLOCKED` | – | – | 있음 |
| `extraction` / `unparsable_value` | `BLOCKED` | – | – | 있음 |
| `extraction` / `unsupported_metric`, `unsupported_accounting_scope` | `BLOCKED` | – | – | 있음 |
| `evidence` / `missing_doc_id` | `BLOCKED` | – | – | 있음 |
| `filing_discovery` / `manifest_missing`, `path_unresolved`, `main_document_missing`, `unknown_document_class`, `unknown_period_kind`, `unparsable_date_range` | `BLOCKED` | – | – | 있음 |
| `query_plan` / `ambiguous_company_match` | `COMPLETED` | `UNRESOLVED` | `AMBIGUOUS_MATCH` (§4 참조) | – |
| `query_plan` / 그 밖의 사유(`unknown_company`, `missing_year`, `unsupported_period`, `unsupported_metric` 등) | `BLOCKED` | – | – | 있음 |
| `routing` / `unimplemented_intent`, `unknown_intent` | `BLOCKED` | – | – | 있음 |
| `contract_adaptation` / `unconvertible_fact` | `BLOCKED` | – | – | 있음 |

성공 시 `FinancialFact` → `Fact`/`Evidence` 변환은 `periodic.adapter.adapt_financial_fact`를
그대로 씁니다. 같은 변환을 정규화 계층에서 다시 구현하면 두 벌이 되고, 식별자가 갈리면
`Fact.evidence_ids` ↔ `Evidence.evidence_id` 연결도 함께 끊깁니다.

### `missing_acontext` 계열을 BLOCKED로 두는 이유

이 사유들의 `detail`은 "Parser가 …를 제공해야 합니다", "그대로 쓰면 자릿수 오류가
발생합니다"라고 밝힙니다. 값은 문서 안에 있는데 파이프라인이 안전하게 읽어내지 못한
상태이며, 코퍼스에 대한 판정이 아닙니다.

### `query_plan` 단계에서 하나만 `COMPLETED`인 이유

`ambiguous_company_match`는 인식 후보 목록 전체와 대조한 뒤에 나오는 판정입니다. manifest가
아는 표기를 모두 보고 "여러 곳이 걸렸고 그중 하나를 고를 근거가 없다"까지 확정했으므로,
판정에 이르지 못하고 멈춘 것이 아닙니다. 문서 목록을 모두 보고 내리는
`filing_discovery`/`ambiguous_root`와 같은 자리이며, 코퍼스를 여느냐 인식 후보 목록을
보느냐만 다릅니다.

같은 단계의 나머지 사유는 질문을 계획으로 옮기지 못한 것입니다. `unknown_company`는 "그
기업이 이 코퍼스에 없다"이고, `missing_year`·`unsupported_period`는 조회 조건을 만들지
못한 것입니다. 코퍼스에 답이 있는지에 대한 판정이 아니므로 `BLOCKED`로 남습니다.

두 갈래를 나누지 않으면 되묻기로 풀리는 질문("삼성 2025년 매출 알려줘.")과 코퍼스에 없는
기업을 물은 질문이 같은 상태로 보고됩니다.

어느 후보들 사이에서 모호했는지는 `answerability_detail`의 원문에 남습니다. 후보 중 하나를
고르지 않으며, 이 판정 뒤에는 Fact·Evidence 조회가 일어나지 않습니다.

### `contract_adaptation` 단계

`periodic.adapter`가 요구하는 `metadata.doc_id` / `metadata.corp_name`이 없거나 Contract
enum에 없는 값이면 `ValueError`가 올라옵니다. 값을 추측해 채우지 않고 이 단계에서 막힌
것으로 남깁니다. 실측 코퍼스(FY2024 정기공시 70사)에서는 추출에 성공한 45건 전부가
변환에도 성공해 이 경로가 발생하지 않았습니다.

## 3. Pattern E — 정정 이력

| 상황 | execution_status | answerability_status | answerability_reason |
|---|---|---|---|
| 체인 완결 | `COMPLETED` | `ANSWERABLE` | – |
| 체인 미완결(`chain_complete=False`) | `COMPLETED` | `PARTIALLY_ANSWERABLE` | `INCOMPLETE_CHAIN` |
| `filing_discovery` / `no_matching_filing` | `COMPLETED` | `UNANSWERABLE` | `NOT_FOUND` |
| `filing_discovery` / `no_original_filing` | `COMPLETED` | `UNANSWERABLE` | `NOT_FOUND` |
| `filing_discovery` / `ambiguous_root` | `COMPLETED` | `UNRESOLVED` | `AMBIGUOUS_MATCH` (§4 참조) |
| `conversion.history is None` (루트 미확정) | `COMPLETED` | `UNRESOLVED` | **없음** |
| `parsing` / `parse_failed` | `BLOCKED` | – | – |
| `filing_discovery` / `manifest_missing` 등 | `BLOCKED` | – | – |

`EventHistoryResult.blocked`가 있는지 여부만으로 정하지 않습니다. `no_matching_filing`과
`ambiguous_root`는 `blocked`가 채워져 있어도 코퍼스를 끝까지 본 뒤의 판정이므로 실행은
끝난 것입니다.

이미 Contract 타입인 `EventHistory` / `EventLink` / `Evidence`는 다시 만들지 않고 그대로
싣습니다. 재변환하면 `event.contract_adapter`가 이미 내린 판정을 두 번 내리게 됩니다.

`INCOMPLETE_CHAIN`은 새 판정이 아니라 `EventHistory.chain_complete=False`를 기계가 읽을 수
있는 사유로 옮긴 것입니다. 무엇을 연결하지 못했는지는 `EventLink.resolution_status`와
`conversion_gaps`에 이미 남아 있습니다.

**위 표는 현재 producer가 실제로 만드는 조합만 담습니다.** `AnswerabilityReason` 전체를 덮지
않습니다. `WITHHELD_IN_SOURCE`는 Contract와 Answer Builder 대응이 있지만
([open_decisions.md](open_decisions.md) D9) 유보를 감지하는 producer가 아직 없어 이 표에 행이
없습니다. 감지 경로가 생기면 상태 대응(일부 유보 → `PARTIALLY_ANSWERABLE`, 전체 유보 →
`UNANSWERABLE`)과 함께 행을 추가합니다.

`WITHHELD_IN_SOURCE`가 실린 결과에는 `chain_complete` 불변식을 적용하지 않습니다. 체인 완결과
원문 유보는 독립된 축이므로, 완결된 체인 안의 일부 field만 유보인 결과가 성립합니다.

## 4. `AMBIGUOUS_MATCH`와 `AMBIGUOUS_LINK`를 섞지 않는 이유

`AMBIGUOUS_LINK`는 [evidence_and_answerability.md](evidence_and_answerability.md)에서
**"정정 대상 문서를 1건으로 확정할 수 없음"** 으로 정의됐고, Contract에서도
`EventLinkResolutionStatus.AMBIGUOUS`와 같은 어휘를 씁니다. 즉 문서 사이의 **연결**이
모호하다는 뜻이며, 그 판정은 `event.linker`가 내려 `EventLink.resolution_status`로 실려
나갑니다.

위 두 사유는 연결 판정이 아닙니다.

- `ambiguous_candidates` — 문서 하나 안에서 본표와 주석의 셀 후보를 좁히지 못한 것
- `ambiguous_root` — 질문이 어느 계약을 가리키는지 좁히지 못한 것

둘 다 **무엇을 대상으로 삼을지**를 정하지 못한 것이고, 그 결과에는 `EventLink`가 하나도
없습니다. `AMBIGUOUS_LINK`를 실으면 받는 쪽은 "정정 대상을 특정하지 못했다"는 문장을 만들게
되고, 형식은 맞지만 근거가 틀린 답이 됩니다. 그래서 PR #18이 추가한 `AMBIGUOUS_MATCH`를
씁니다.

어느 후보들 사이에서 모호했는지는 canonical reason이 담지 못합니다. Pattern A는
`answerability_detail.detail`의 원문에, Pattern E는 `root_candidate_doc_ids` gap에 남습니다.

## 5. 세 진단 필드의 역할

| 필드 | 역할 | 언제 채우는가 |
|---|---|---|
| `answerability_reason` | 평가·정책이 대조하는 canonical 사유(Contract enum) | `COMPLETED` + 대응 enum이 있을 때 |
| `answerability_detail` | 그 판정을 내린 Agent 내부 진단 원문 `{stage, reason, detail}` | `COMPLETED` + 멈춘 지점이 있을 때 |
| `execution_issue` | 실행이 중단된 지점 | `BLOCKED` / `FAILED`일 때만 |
| `conversion_gaps` | Contract에 실을 자리가 없어 **손실되는** 정보 | 손실이 있을 때만 |

canonical reason은 서로 다른 정지 사유를 한 값으로 묶습니다. `NOT_FOUND`만 보면
`no_matching_filing`인지 `no_original_filing`인지 알 수 없고 후속 조치가 다르므로,
`answerability_detail`이 그 구분을 원문 그대로 남깁니다.

판정 근거는 실을 자리가 있으므로 **손실이 아니며, `conversion_gaps`에 남기지 않습니다.**

### conversion_gaps에 남는 것

| kind | 언제 | 담는 것 |
|---|---|---|
| `root_candidate_doc_ids` | Pattern E에서 `EventHistory`를 만들지 못했을 때 | 원공시 후보 `doc_id` 목록 |
| `raw_field_name`, `change_source_document`, `unmapped_field_name`, `unresolved_links`, `pending_event_id`, `pending_resolution_status` | `event.contract_adapter`가 만든 것 | 필드를 그대로 옮깁니다. 문자열로 합치지 않습니다 |

Pattern E에서 `EventHistory`를 만든 경우에는 루트 `doc_id`가 `event_id`
(`event_{event_type}_{root_doc_id}`)에 들어가므로 후보 gap을 만들지 않습니다.

## 6. ExecutionStatus.FAILED

**이번 구현은 `FAILED`를 만들지 않습니다.**

Pattern A/E는 예상한 실패를 전부 내부 `BlockedInfo`로 돌려주고, 예상하지 못한 예외는 삼키지
않고 호출자에게 올립니다. 넓은 `except`로 감싸 `FAILED`를 억지로 만들면 원인이 사라집니다.
그 예외를 `FAILED`로 옮길지는 API/Runtime 통합 경계의 결정 사항입니다.

## 7. 공통 Contract에 담기지 않는 것

정규화 이후 아래 정보는 `AgentExecutionResult`에 남지 않습니다. 필요하면 Pattern 내부
결과(`SingleFactResult`, `EventHistoryResult`)를 직접 씁니다.

| 정보 | 어디에 있었나 | 현재 상태 |
|---|---|---|
| `QueryPlan` | 모든 결과 | 싣지 않음(설계 결정). 질문 해석 결과이지 실행 결과가 아님 |
| 공시 메타데이터(`receipt_no`, `report_name`, `receipt_date`, `source_path`, `base_year`, `is_correction`, `corp_code`, `stock_code`) | Pattern A `filing` | 손실. `Fact.company`와 `Evidence.doc_id`만 남음 |
| `acode`, `acontext`, `unit_text`, `section_id`, 표 분류 | `agent.evidence.Evidence` | PR #18이 `Evidence.provenance`(`EvidenceProvenance`)로 실을 자리를 만들었으나 `periodic.adapter`가 아직 채우지 않아 `provenance=None`. 정규화 계층은 adapter가 만든 `Evidence`를 그대로 전달하며, 값을 지어내지 않음 |
| `Event` 객체 | – | Pattern E가 만들지 않음(미보유, 손실 아님). PR #19의 `event/extractor.py`는 아직 어느 handler에도 연결돼 있지 않고 Contract `Event`가 아니라 dict를 돌려주므로, 연결은 Pattern E 동작 변경이며 별도 PR 대상 |

`EvidenceProvenance`를 채우는 것은 `periodic.adapter`의 책임이며 별도 PR에서 다룹니다.
정규화 계층이 대신 채우면 같은 변환이 두 벌이 되고, 어느 쪽이 정본인지 알 수 없게 됩니다.
