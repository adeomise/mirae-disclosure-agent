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
| Q03 | A | `NOT_IMPLEMENTED` | – | 지분공시(`document_class=holding`) 미지원. `MetricConcept`에 값은 있으나 analyzer가 인식하지 않음. `query_plan/unsupported_metric`으로 BLOCKED | D6(PII 처리) 확정 전에는 착수하지 않음 |
| Q04 | B | `NOT_IMPLEMENTED` | – | `document_summary` handler 없음. `query_plan/unsupported_metric`으로 BLOCKED | Pattern B 착수 시. C13 Vector fallback 여부는 D7 |
| Q05 | B | `NOT_IMPLEMENTED` | – | 같음 + 기간 미지정 기본 문서 정책 미확정(D5-a). `query_plan/missing_year`로 BLOCKED | D5-a 확정 필요 |
| Q06 | B | `NOT_IMPLEMENTED` | – | 같음 + 판본 선택 정책 미확정(D5). `query_plan/missing_year`로 BLOCKED | D5 확정 필요 |
| Q07 | C | `NOT_IMPLEMENTED` | – | `temporal_compare` 미분류·미등록. 상대 기간("작년") 해석 없어 `query_plan/missing_year`로 BLOCKED. **상대 기간 해석을 넣는 순간 F4("내년 매출") 질문이 통과함** | Pattern C 착수. 미래 방향 차단(Pattern F)과 같은 PR로 묶어야 함 |
| Q08 | C | `NOT_IMPLEMENTED` | – | 영업이익률 파생지표 미지원. `query_plan/unsupported_metric`으로 BLOCKED(사유는 정확) | `operating_income` 추출 + `agent/calculator` 연결 |
| Q09 | C | `NOT_IMPLEMENTED` | – | 서술 비교(C14) 없음. `query_plan/unsupported_metric`으로 BLOCKED | Pattern B/C 이후 |
| Q10 | D | `NOT_IMPLEMENTED` **+ 오라우팅** | – | **`COMPLETED`/`ANSWERABLE`로 Fact 1건을 돌려줌.** 두 기업 비교 질문인데 `_extract_company`가 가장 긴 이름 하나만 골라 한 기업 값만 답하고, 나머지 기업이 사라진 흔적이 `warnings`·`conversion_gaps` 어디에도 없음 | **즉시 조치 대상.** `cross_entity_compare`로 분류해 `routing/unimplemented_intent`로 막기 |
| Q11 | D | `NOT_IMPLEMENTED` | – | 영업이익률 미지원으로 `query_plan/unsupported_metric` BLOCKED. Q10과 달리 지표에서 먼저 걸려 오라우팅되지 않음 | Q08과 함께 |
| Q12 | D | `NOT_IMPLEMENTED` | – | 신규시설투자 subtype 미지원 + 집계 경로 없음. `query_plan/unsupported_metric`으로 BLOCKED | D1 확정 후. Q15 선행 |
| Q13 | E | `PASS_ACTUAL` | Actual | 없음. `COMPLETED`/`ANSWERABLE`, EventHistory 1 · EventLink 2 · Evidence 9, `chain_complete=True` | 회귀 fixture에 고정 완료(`Q13`) |
| Q14 | E | `NOT_IMPLEMENTED` | Synthetic(능력 일부) | 질문이 연/월을 주지 않아 `query_plan/missing_year`로 BLOCKED. 계약상대방("포드")으로 루트를 좁히는 C3 없음. 체인 미완결 → `PARTIALLY_ANSWERABLE` 경로는 `tests/integration/test_pattern_e.py`에서 합성 검증됨 | 계약상대방 기반 루트 확정. 공시유보(`WITHHELD_IN_SOURCE`)는 Contract 미결정 |
| Q15 | E | `NOT_IMPLEMENTED` | Synthetic(능력 일부) | 연도 없음 + 신규시설투자 subtype 미지원으로 BLOCKED. 다중 정정 정렬·필드별 궤적은 합성 fixture로 검증됨 | `EVENT_SUBTYPE_TOKENS`에 신규시설투자 추가. D1 확정 후 |
| Q16 | F | `PARTIAL` | Actual | 거부되긴 하나 사유가 다름. `query_plan/unsupported_period`(분기·반기 미지원)로 BLOCKED이며 **기간 밖 판정이 아님**. 연 단위로 물으면(`2027년 매출액`) 방어가 사라짐 | Pattern F Gate. 사유 코드는 F-D1 미결정 |
| Q17 | F | `PARTIAL` | Actual | `query_plan/unknown_company`로 BLOCKED. 기업명이 없어서 막힌 것이지 "시장 반응은 보유 문서군이 아니다"라는 판정이 아님 | 같음 |
| Q18 | F | `PARTIAL` | Actual | `query_plan/missing_year`로 BLOCKED. 연도가 없어서 막힌 것이지 정책 판정이 아님. **`삼성전자 2024년 매출 기준으로 지금 사도 될까?`는 그대로 통과함** | 같음. 정책 판정은 문서 접근 이전 단계여야 함 |

## 집계

| 상태 | 문항 수 | 문항 |
|---|---|---|
| `PASS_ACTUAL` | 2 | Q01 Q13 |
| `PARTIAL` | 3 | Q16 Q17 Q18 |
| `NOT_IMPLEMENTED` | 13 | Q02~Q12 Q14 Q15 |
| `PASS_SYNTHETIC_ONLY` | 0 | – |
| `BLOCKED_BY_DATA` | 0 | – |
| `BLOCKED_BY_CONTRACT` | 0 | – |

`BLOCKED_BY_CONTRACT`가 0인 이유는, Contract 값이 없는 판정(F1~F4의 사유 코드)이 지금은
**결과를 만들지 못하게 막는 것이 아니라 `answerability_reason`을 비우고 판정 근거를
`answerability_detail`(stage/reason/detail)에 남기는 방식**으로 처리되고 있기 때문입니다.
문항 단위로는 막혀 있지 않습니다.

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
| C 시계열 비교 | Q07 Q08 Q09 | 0/3. **상대 기간 해석과 Pattern F가 충돌** |
| D 기업 간 비교 | Q10 Q11 Q12 | 0/3. **Q10은 오라우팅으로 잘못된 답을 만듦** |
| E 정정·후속 추적 | Q13 Q14 Q15 | 1/3 실측 통과. 공급계약 + 연/월 지정 형태만 |
| F 답변 불가 | Q16 Q17 Q18 | 0/3 정상 판정. 3건 모두 우연히 거부됨 |

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
2. **`PARTIAL` 3건을 "Pattern F가 동작한다"의 근거로 쓰지 않습니다.** 거부 사유가 전부
   무관한 파싱 실패입니다. 자세한 내용은 [pattern_f_safety.md](pattern_f_safety.md) 부록.
3. **오라우팅 항목(Q10)은 미구현보다 나쁩니다.** 미구현은 답을 안 하지만, 오라우팅은 근거를
   붙인 틀린 답을 합니다.

## 관련 문서

- 대표 질문 원문: [representative_questions.md](representative_questions.md)
- 실행 Pattern: [agent_patterns.md](agent_patterns.md)
- 실행 결과 대응표: [execution_result_mapping.md](execution_result_mapping.md)
- 안전·거부 정책: [pattern_f_safety.md](pattern_f_safety.md)
- 미결정 사항: [open_decisions.md](open_decisions.md)
