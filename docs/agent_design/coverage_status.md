# 대표 질문 18문항 커버리지 현황

`representative_questions.md`의 18문항이 **지금 실제로 어디까지 동작하는지**를 실측으로
기록합니다. 설계 의도가 아니라 측정 결과입니다.

- 측정일: 2026-08-24
- 측정 방법: `disclosure_agent.agent.router.answer_question(질문, corpus_root=<실측 코퍼스>)`
  호출 후 `AgentExecutionResult` 확인
- 재현: `python scripts/run_corpus_regression.py --corpus-root <path>`
- 측정 대상: PR 브랜치 `feat/safety-regression-harness` (base: `develop`)
- 측정 기준선: 측정 시점의 `develop` = `1e7f1f0`
  (PR #21 `feat/agent-execution-normalization` 병합. Pattern A/E 정규화 반영 후)
- 현재 이 브랜치가 병합한 `develop` = `1f19a72`
  (PR #24 `feat/periodic-operating-income` 병합, 2026-08-25). 그 이후 반영분은 아래 표에
  재측정되지 않았습니다.
- **Q16~Q18 재측정: 2026-08-25**, 브랜치 `feat/f3-f4-safety-gate`
  (F3·F4 pre-retrieval Gate + PR #29 `AnswerabilityReason` 반영분).
- **Q08~Q09 재측정: 2026-08-26**, 브랜치 `fix/multi-period-silent-wrong`
  (다중 기간 질문 fail-closed 가드 반영분, base `develop` = `eb70f12`). 두 문항의 정지 지점이
  `query_plan/unsupported_metric`에서 `routing/unimplemented_intent`로 바뀌어 아래 표를
  갱신했습니다. 지원 상태(`NOT_IMPLEMENTED`)는 그대로입니다.
- **Q14 재측정: 2026-09-04**, 브랜치 `fix/hcx-v3-transport-readiness`
  (연도 없는 Pattern E 계획을 허용한 반영분). 정지 지점이 `query_plan/missing_year`에서
  `filing_discovery`/`ambiguous_root`로 바뀌어 아래 표를 갱신했습니다. 지원 상태도
  `NOT_IMPLEMENTED`에서 `PASS_ACTUAL`로 바뀝니다 — 이 문항의 정답이 답변이 아니라 모호
  판정이고(D8), 그 판정을 실측 코퍼스에서 확인했기 때문입니다.
- 위에 적은 문항 외에는 재측정하지 않았습니다. 나머지 문항의 값은 최초 측정일 기준입니다.

본 문서에는 실제 공시 값·접수번호를 기재하지 않습니다. 상태와 건수만 기록합니다.

## 상태 표기

| 표기 | 의미 |
|---|---|
| `PASS_ACTUAL` | 실측 코퍼스로 기대한 Contract 결과를 확인함 |
| `PASS_SYNTHETIC_ONLY` | 합성 fixture 테스트만 통과. 실측 검증 없음 |
| `PARTIAL` | 결과 상태는 기대에 가깝지만 사유·근거가 기대와 다름 |
| `NOT_IMPLEMENTED` | 해당 Pattern·capability의 handler가 없음 |
| `BLOCKED_BY_DATA` | 구현과 무관하게 코퍼스가 요구를 만족하지 못함 |
| `BLOCKED_BY_CONTRACT` | Contract 값이 없어 결과를 표현할 수 없음 |

**임의로 PASS를 주지 않았습니다.** 합성 테스트만 있는 문항은 `PASS_SYNTHETIC_ONLY`가 아니라,
그 문항의 **질문 문자열이 실제로 처리되지 않으면** `NOT_IMPLEMENTED`입니다. 능력 일부가 합성
테스트로 검증된 경우는 Main blocker 칸에 따로 적었습니다.

## 커버리지 매트릭스

| ID | Pattern | Current support | Actual/Synthetic | Main blocker | Next action |
|---|---|---|---|---|---|
| Q01 | A | `PASS_ACTUAL` | Actual | 없음. FY2025·FY2024·별도 기준 모두 `COMPLETED`/`ANSWERABLE`, Fact 1 · Evidence 1, 연결 유효 | 회귀 fixture에 고정 완료(`Q01`, `Q01-FY2024`, `Q01-SEPARATE`) |
| Q02 | A | `NOT_IMPLEMENTED` | – | 거래소공시 단건 필드 조회 경로 없음. 현재 `query_plan/unsupported_metric`으로 BLOCKED. 계약상대방으로 문서를 좁히는 C3도 없음 | Pattern A에 event 단건 조회 추가. D1(거래소공시 Parser) 확정 후 |
| Q03 | A | `NOT_IMPLEMENTED` | – | 지분공시(`document_class=holding`) 미지원. `MetricConcept`에 값은 있으나 analyzer가 인식하지 않음. `query_plan/unsupported_metric`으로 BLOCKED | **D6-a 확정됨.** allowlist(`THS_STK_CNT`, `THS_STK_RT`) 기반 extractor 구현. D6-a의 acceptance criteria 4개를 만족해야 함 |
| Q04 | B | `NOT_IMPLEMENTED` | – | `document_summary` handler 없음. `query_plan/unsupported_metric`으로 BLOCKED | Pattern B 착수 시. C13 Vector fallback 여부는 D7 |
| Q05 | B | `NOT_IMPLEMENTED` | – | 같음 + 기간 미지정 기본 문서 정책 미확정(D5-a). `query_plan/missing_year`로 BLOCKED | D5-a 확정 필요 |
| Q06 | B | `NOT_IMPLEMENTED` | – | 같음 + 판본 선택 정책 미확정(D5). `query_plan/missing_year`로 BLOCKED | D5 확정 필요 |
| Q07 | C | `NOT_IMPLEMENTED` | – | 상대 기간 해석은 끝남(D8 확정). "작년"이 FY2024·FY2025로 옮겨져 `temporal_compare` 계획까지 만들어지며, 이제 `pattern_entry/unsupported_comparison_metric`으로 BLOCKED. 남은 것은 원천 지표의 증감(`delta`·`delta_pct`) 계산이며 그것이 Q07-2 범위. F4 충돌은 없음 — 미래 방향은 해석하지 않고 safety Gate가 질의 해석보다 앞에 있음(F-D6 해소) | Pattern C에 원천 지표 증감 추가 |
| Q08 | C | `NOT_IMPLEMENTED` | – | 다중 기간으로 인식되어 `routing/unimplemented_intent`로 BLOCKED. 영업이익률 파생지표는 여전히 미지원이지만, 더 근본적인 미구현(Pattern C)이 먼저 판정됨 | `operating_income` 질의 어휘 + `agent/calculator` 연결 |
| Q09 | C | `NOT_IMPLEMENTED` | – | 다중 기간으로 인식되어 `routing/unimplemented_intent`로 BLOCKED. 서술 비교(C14)는 여전히 없음 | Pattern B/C 이후 |
| Q10 | D | `NOT_IMPLEMENTED` | – | 오라우팅 해소됨. analyzer가 매치된 서로 다른 기업을 모두 인식해 `cross_entity_compare`로 분류하고, handler가 없으므로 `routing/unimplemented_intent`로 BLOCKED(Fact 0 · Evidence 0). 이전에는 `_extract_company`가 가장 긴 이름 하나만 골라 `COMPLETED`/`ANSWERABLE`로 한 기업 값만 답했음 | Pattern D handler 착수. 차단 동작은 회귀 fixture에 고정 완료(`GUARD-MULTI-COMPANY`) |
| Q11 | D | `NOT_IMPLEMENTED` | – | 영업이익률 미지원으로 `query_plan/unsupported_metric` BLOCKED. Q10과 달리 지표에서 먼저 걸려 오라우팅되지 않음 | Q08과 함께 |
| Q12 | D | `NOT_IMPLEMENTED` | – | 신규시설투자 subtype은 Q15에서 지원됐으나 집계 경로가 없음. 기업이 둘이라 `cross_entity_compare`로 분류되고 handler가 없어 `routing/unimplemented_intent`로 BLOCKED. 이력 표현이 없어 Pattern E로 새지도 않음 | 거래소공시 집계 경로. D1 확정 후 |
| Q13 | E | `PASS_ACTUAL` | Actual | 없음. `COMPLETED`/`ANSWERABLE`, EventHistory 1 · EventLink 2 · Evidence 9, `chain_complete=True` | 회귀 fixture에 고정 완료(`Q13`) |
| Q14 | E | `PASS_ACTUAL` | Actual | 없음. **정답이 답변 아님**(D8)이며 그 정답을 실측으로 확인함. `COMPLETED`/`UNRESOLVED`/`AMBIGUOUS_MATCH`, 정지 지점 `filing_discovery`/`ambiguous_root`, 원공시 후보 12건에서 루트를 고르지 않음 — EventHistory 0 · EventLink 0 · Evidence 0. API는 HTTP 200(실행이 끝나지 못한 BLOCKED가 아니라 끝난 뒤의 미해결 판정이므로 422가 아님). 이전에는 `query_plan/missing_year`로 막혔고 그 사유는 실제로 일어난 일과 달랐음 — 질문이 연도를 밝히지 않은 것이 원인이 아니라 루트를 하나로 좁힐 조건이 없는 것이 원인이며, 연도 없는 Pattern E 계획을 허용해 판정을 후보 개수가 보이는 handler로 옮김. **계약상대방 검증은 여전히 없음(C3)** — 원공시가 1건뿐인 기업에서는 상대방을 본문과 대조하지 않은 채 답이 나가며, 그 잔여 경계는 `tests/integration/test_pattern_e_q15.py`에 고정 | C3(계약상대방 기반 문서 확정). 루트가 1건으로 좁혀진 뒤에야 공시유보(`WITHHELD_IN_SOURCE`, **Contract 확정됨** D9, production 판정은 미구현)와 역방향·후속 연결 체인이 검증 대상이 됨. 회귀 fixture(`scripts/corpus_regression_cases.jsonl`) 등재는 미완 — 현재는 `tests/integration/test_pattern_e_q15.py`·`tests/integration/test_api_q14_corpus.py`가 고정 |
| Q15 | E | `PASS_ACTUAL` | Actual | 없음. `COMPLETED`/`ANSWERABLE`, EventHistory 1 · EventLink 3 · Evidence 8, `chain_complete=True`. 질문에 연도가 없어도 공시일 창 없이 루트를 찾음. 공시 유형으로 가르지 않는다 — Pattern E는 유형과 무관하게 공시일 기준점 없는 계획을 만들고, 루트를 하나로 좁힐 수 있는지는 handler가 실제 후보 개수로 판정한다(후보가 여럿이면 `ambiguous_root`). 이 질문은 후보가 1건이라 답에 이른다. **"몇 번 바뀌었나"를 정정 3건으로 셀지 FieldChange 4건으로 셀지는 미정** — producer는 둘 다 보존하며 세는 규칙은 answer-builder 결정 | 회귀 fixture에 고정 완료(`Q15`). lifecycle은 `future` 유지 — active 전환은 "몇 번" 세는 규칙 확정과 함께 |
| Q16 | F | `PARTIAL` | Actual | 거부되긴 하나 사유가 다름. `query_plan/unsupported_period`(분기·반기 미지원)로 BLOCKED이며 **기간 밖 판정이 아님**. 연 단위로 물으면(`삼성전자 2027년 매출액이 얼마야?`) 이 차단이 사라지고 `COMPLETED`/`UNANSWERABLE`/`not_found`(`filing_discovery`/`no_matching_filing`)로 답합니다 — 검색 실패이지 기간 밖 판정이 아님 | F1 Gate 구현. 사유 코드는 확정됨(`OUT_OF_DATA_RANGE`, PR #29) |
| Q17 | F | `PARTIAL` | Actual | `query_plan/unknown_company`로 BLOCKED. 기업명이 없어서 막힌 것이지 "시장 반응은 보유 문서군이 아니다"라는 판정이 아님 | F2 Gate 구현. 사유 코드는 확정됨(`OUT_OF_SCOPE_DATA`, PR #29) |
| Q18 | F | `PASS_ACTUAL` | Actual | 없음. F3 Gate가 retrieval 이전에 판정. `COMPLETED`/`UNANSWERABLE`/`out_of_scope_policy`, `answerability_detail`=`answerability_gate`/`investment_advice`, Fact 0 · Evidence 0. 이전에는 `query_plan/missing_year`(연도 부재)로 막힌 우연한 방어였고 **`삼성전자 2024년 매출 기준으로 지금 사도 될까?`는 그대로 통과**했으나, 그 형태도 같은 결과로 거부됨 | 판정 동작은 `tests/unit/test_pattern_f_safety_gate.py`에 고정 완료 |

## 집계

| 상태 | 문항 수 | 문항 |
|---|---|---|
| `PASS_ACTUAL` | 4 | Q01 Q13 Q15 Q18 |
| `PARTIAL` | 2 | Q16 Q17 |
| `NOT_IMPLEMENTED` | 12 | Q02~Q12 Q14 |
| `PASS_SYNTHETIC_ONLY` | 0 | – |
| `BLOCKED_BY_DATA` | 0 | – |
| `BLOCKED_BY_CONTRACT` | 0 | – |

`BLOCKED_BY_CONTRACT`가 0입니다. F1~F4의 사유 코드는 PR #29에서
`OUT_OF_DATA_RANGE` · `OUT_OF_SCOPE_DATA` · `OUT_OF_SCOPE_POLICY`가 추가되어 **Contract에
자리가 있습니다.** Q18은 그 값(`out_of_scope_policy`)을 실제로 싣고 나오고, Q16·Q17이 아직
`PARTIAL`인 것은 Contract 때문이 아니라 **판정 자체가 구현되지 않았기 때문**입니다.

`WITHHELD_IN_SOURCE`와 `NOT_COMPARABLE`도 이제 Contract에 자리가 있습니다. `NOT_COMPARABLE`은
Pattern D 작업에서, `WITHHELD_IN_SOURCE`는 D9 확정과 함께 추가됐습니다. 두 사유 모두
`answerability_reason`을 비우지 않고 그대로 싣습니다.

`NOT_COMPARABLE`은 production 판정까지 있고(`agent/normalization.py`의 `STAGE_COMPARABILITY`),
`WITHHELD_IN_SOURCE`는 **Contract와 Answer Builder 대응만 있고 production 판정은 아직
없습니다.** 유보를 감지하는 쪽은 Event Parser 결과에 의존하며 별도 PR입니다.

**대응 값이 없는 판정은 현재 없습니다.** 새로 생기면 `answerability_reason`을 비우고 판정 근거를
`answerability_detail`(stage/reason/detail)에 남기는 기존 규약을 씁니다.

`ambiguous` 판정은 여기 해당하지 않습니다. Contract v0.1에 대응 값이 있어 그대로 표현됩니다.

- `extraction` / `ambiguous_candidates`, `filing_discovery` / `ambiguous_root`
  → `ExecutionStatus.COMPLETED` · `AnswerabilityStatus.UNRESOLVED` ·
  `AnswerabilityReason.AMBIGUOUS_MATCH`
- 진단 정보는 `answerability_detail`(stage/reason/detail)에 기록합니다.
- `conversion_gaps`는 Contract에 실을 자리가 없어 손실되는 정보 전용이므로 판정 근거를
  담지 않습니다.
- 문서 사이의 **연결**이 모호한 경우는 그대로 `AMBIGUOUS_LINK`이며, `event.linker`가 판정해
  `EventLink.resolution_status`로 실려 나갑니다.

두 사유의 구분 근거는 [execution_result_mapping.md](execution_result_mapping.md) §4에 있습니다.

## Pattern별 요약

| Pattern | 문항 | 상태 |
|---|---|---|
| A 단일 사실 | Q01 Q02 Q03 | 1/3 실측 통과. 정기공시 매출만 |
| B 문서 요약 | Q04 Q05 Q06 | 0/3. handler 없음 |
| C 시계열 비교 | Q07 Q08 Q09 | 0/3. 상대 기간 해석은 완료(D8). Pattern F와 충돌하지 않음 — 과거 방향만 해석하고 Gate가 앞에 있음 |
| D 기업 간 비교 | Q10 Q11 Q12 | 0/3. handler 없음. Q10 오라우팅은 해소되어 `routing`에서 BLOCKED |
| E 정정·후속 추적 | Q13 Q14 Q15 | 2/3 실측 통과. 공급계약(연/월 지정)과 신규시설투자(연도 없음). 한 질문이 두 유형에 맞으면 고르지 않고 `query_plan/ambiguous_event_subtype`으로 BLOCKED |
| F 답변 불가 | Q16 Q17 Q18 | 1/3 정상 판정. Q18은 F3 Gate가 retrieval 이전에 판정. Q16·Q17은 여전히 우연히 거부됨(F1·F2 미구현) |

## Q01 계열 실측 스윕

`execution_result_mapping.md`가 근거로 삼은 FY2024 정기공시 70사 스윕과 같은 조건으로
재측정한 결과입니다. 질문 형태는 `"<기업> 2024년 연결 기준 매출액이 얼마야?"` 하나입니다.

| 결과 | 건수 | 비율 | 의미 |
|---|---|---|---|
| `COMPLETED` / `ANSWERABLE` | 45 | 64% | Fact 1건 + Evidence 1건 확보 |
| `BLOCKED` / `parsing` `parse_failed` | 23 | 33% | **문서를 열지 못함.** 답이 없는지조차 모름 |
| `COMPLETED` / `UNANSWERABLE` / `insufficient_evidence` | 2 | 3% | 문서를 끝까지 읽었으나 해당 셀이 없음 |

`execution_result_mapping.md`가 기록한 45건과 일치합니다. 주목할 것은 **실패 25건의 구성**입니다.

- **23건이 `parse_failed`입니다.** 검색이나 판정 문제가 아니라 Parser가 문서를 열지 못한
  것입니다. Pattern A의 로직은 이미 맞고, 남은 것은 정기공시 Parser의 커버리지입니다.
- **2건만이 실제 추출 실패입니다.** 즉 파싱만 통과하면 현재 추출 경로가 거의 그대로 통합니다.

이 구성은 우선순위 판단에 직접 쓰입니다. Pattern B~D를 새로 만드는 것보다 `parse_failed` 23건을
줄이는 쪽이 같은 코드로 커버되는 기업 수를 더 크게 늘립니다.

이 수치는 **Pattern A의 실제 커버리지**를 뜻하며, 대표 질문 Q01 한 건의 통과와 구분해서
읽어야 합니다. Q01은 한미반도체 하나에 대한 검증이고, 스윕은 같은 코드가 70개 기업의 서로
다른 문서 구조에서 어떻게 갈리는지를 봅니다.

## 이 문서를 어떻게 쓰나

1. **`PASS_ACTUAL`만 "된다"로 셉니다.** 합성 테스트 통과는 회귀 방지이지 동작 증거가
   아닙니다. `pytest`가 통과하는 것과 실측 코퍼스에서 동작하는 것은 별개이며, `pytest`
   경로에서 실측 코퍼스를 쓰는 것은 `tests/unit/test_pattern_f_safety_cases.py`의
   현재 동작 대조 테스트뿐입니다(코퍼스가 없으면 건너뜁니다). 실측 검증의 정본은
   `scripts/run_corpus_regression.py`입니다.
2. **`PARTIAL` 2건(Q16·Q17)을 "Pattern F가 동작한다"의 근거로 쓰지 않습니다.** 거부 사유가
   무관한 파싱 실패입니다. Pattern F에서 판정으로 거부되는 것은 Q18(F3)뿐입니다. 자세한
   내용은 [pattern_f_safety.md](pattern_f_safety.md) 부록.
3. **오라우팅은 미구현보다 나쁩니다.** 미구현은 답을 안 하지만, 오라우팅은 근거를 붙인 틀린
   답을 합니다. Q10(기업 다중 값)과 `F6-02`(Q07 계열 기간 다중 값)가 같은 성격이었고 둘 다
   해소됐습니다. "삼성전자 2024년 매출과 2025년 매출을 비교해줘"는 이제 첫 연도만
   `ANSWERABLE`로 답하지 않고 `routing`/`unimplemented_intent`로 막힙니다. 회귀 감시는
   `scripts/corpus_regression_cases.jsonl`의 `GUARD-MULTI-COMPANY`와 `GUARD-MULTI-PERIOD`가
   맡습니다. 두 Pattern(C·D) 자체는 아직 구현되지 않았습니다.

## 관련 문서

- 대표 질문 원문: [representative_questions.md](representative_questions.md)
- 실행 Pattern: [agent_patterns.md](agent_patterns.md)
- 실행 결과 대응표: [execution_result_mapping.md](execution_result_mapping.md)
- 안전·거부 정책: [pattern_f_safety.md](pattern_f_safety.md)
- 미결정 사항: [open_decisions.md](open_decisions.md)
