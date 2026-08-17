# 검색 전략

18개 대표 질문을 기준으로 한 검색 흐름 설계입니다. 실제 Retrieval 구현이나 검색 엔진·Vector
저장소 선택은 이 문서의 범위가 아닙니다.

문서군 표기는 [query_plan.md](query_plan.md)의 대응(`periodic`=정기공시, `event`=거래소공시,
`holding`=지분공시)을 따르고, 문서 내부 구조는 **Section**으로 표기합니다. Capability 이름은
[agent_patterns.md](agent_patterns.md)를 정본으로 사용합니다.

## 기본 흐름

```text
Query Analysis
→ Metadata Filtering
→ Document Candidate Selection
→ Structural Retrieval
→ Keyword / Vector Search if needed
```

## 설계 원칙

1. **Vector Search를 기본 진입점으로 두지 않습니다.** 질문을 곧바로 의미 검색에 넣지 않고, 먼저
   메타데이터로 문서를 좁히고 문서 내부 구조로 위치를 잡습니다.
2. **구조가 명확한 질문은 Section·Table·필드 기반 탐색을 우선합니다.** 정기공시는 보고서 서식이
   표준화되어 있어 Section 경로로 위치를 지정할 수 있고, 거래소공시는 번호가 매겨진 필드로
   구성된 것으로 관찰되었습니다.
3. **Vector Search의 위치는 현재 제안 단계입니다.** 18개 대표 질문 분석 기준으로는 structural
   retrieval을 우선하고 Vector Search는 fallback 후보로 제안합니다. `.env.example`의
   `VECTOR_SEARCH_ENABLED=false` 기본값이 이 제안과 정합합니다. **최종 MVP 도입 여부는
   [open_decisions.md](open_decisions.md)의 D7 Pending입니다.**
4. **답변 불가를 판정하는 질문에는 의미 검색을 수행하지 않습니다.** 답이 없는 질문에 의미 검색을
   수행하면 유사도가 높은 무관한 내용이 반환되어 근거 없는 답변을 유도합니다.
5. **검색 실패와 데이터 부재를 구분합니다.** 판정 근거를 검색 결과가 아니라 메타데이터 대조에
   둡니다. 자세한 내용은 [evidence_and_answerability.md](evidence_and_answerability.md)를 따릅니다.

## Pattern별 전략

### Pattern A — Single Fact Lookup (Q01 Q02 Q03)

| 단계 | 내용 |
|---|---|
| Metadata Filter | 기업, 문서군, 세부유형, 회계기간 또는 공시일 범위, 정정 여부 |
| Structural Retrieval | 사용. 정기공시는 Section 경로로 재무제표 Section 지정, 지분공시는 요약 정보 블록, 거래소공시는 번호 필드 |
| Search | 불필요. Q02에서 계약상대방으로 문서를 좁힐 때만 후보 문서 집합 안에서 키워드 매칭 |
| Fallback | 지정 Section에서 Fact 미발견 시 같은 문서의 대체 Section을 시도. 그래도 실패하면 종료하고, **다른 문서로 확장하지 않습니다** |

### Pattern B — Section Summary (Q04 Q05 Q06)

| 단계 | 내용 |
|---|---|
| Metadata Filter | 위와 동일. 기간 미지정 시 기본 문서 선택 정책 적용(Q05, [D5-a](open_decisions.md) Pending). 판본 선택 정책 적용(Q06, [D5](open_decisions.md) Pending) |
| Structural Retrieval | 정기공시는 `section_scope`를 Section 경로로 매핑하는 것이 1순위(**Q04에서 결정적**). 거래소공시(Q06)는 Section 전제 없이 번호 필드 구조를 사용 |
| Search | Section 경로 매핑이 실패할 때만, 해당 문서 내부로 범위를 한정한 키워드 검색. Vector는 선택 |
| Fallback | Section 미발견 시 상위 Section으로 한 단계 확장. 실패하면 종료하고 **문서 전체 요약으로 대체하지 않습니다** |

Q04는 이 원칙이 필요한 이유를 보여주는 사례입니다. "투자"라는 표현으로 문서 전체를 의미 검색하면
재무제표 주석의 투자 관련 항목이 함께 검색되어 요약 범위가 확산됩니다. 사업의 내용 Section 아래
설비 관련 하위 Section으로 범위를 먼저 좁혀야 합니다.

### Pattern C — Temporal Comparison (Q07 Q08 Q09)

| 단계 | 내용 |
|---|---|
| Metadata Filter | 같은 기업으로 회계기간별 독립 조회. 정정 보고서 존재 시 판본 선택 정책 필요([D5](open_decisions.md)) |
| Structural Retrieval | 양쪽 문서에 **같은 Section 경로**를 적용. Q07·Q08은 재무제표 Section, Q09는 사업의 내용 Section |
| Search | 불필요 |
| Fallback | 한쪽 기간 문서가 없으면 **비교 자체를 거부**하고 보유 연도를 안내. 다른 연도로 임의 대체하지 않습니다 |

### Pattern D — Cross-entity Comparison (Q10 Q11 Q12)

| 단계 | 내용 |
|---|---|
| Metadata Filter | 기업별 독립 조회 2회. 같은 기간을 강제. Q12는 **대상 연도에 최초 공시된 투자 건**으로 집합을 정의하므로, 대상 연도 접수 문서만 모으지 않고 이후 연도의 정정까지 함께 회수 |
| Structural Retrieval | 정기공시(Q10 Q11)는 양사에 같은 Section 경로 적용. 거래소공시(Q12)는 번호 필드에서 투자금액을 취득 |
| Search | 불필요 |
| Fallback | 한쪽에 해당 지표 개념이 없으면 **비교 불가로 판정하고 사유를 제시**. 유사 계정으로 대체하지 않습니다. Q12에서 어떤 투자 건의 최신값을 확정하지 못하면 그 건을 임의 값으로 합산하지 않고 **집계에서 제외했음을 명시**합니다 |

**Q12의 집합 정의 절차** — 다른 Pattern과 달리 "무엇을 모을 것인가"가 정답을 좌우합니다.

```text
기업별 거래소공시 조회 (신규시설투자)
→ 최초 공시가 대상 연도에 속하는 투자 건만 선별
→ 각 투자 건에 연결된 정정공시를 건별로 묶음 (별건으로 세지 않음)
→ 건별로 필드 단위 최신값 확정
→ 기업별 합계
→ 기업 간 비교
```

정기공시에서 계정을 맞출 때, 행 이름 텍스트로 매칭하면 같은 개념이 기업마다 다른 이름으로 기재되어
있을 때 실패합니다. 실제 데이터 사전 검토에서 표준 계정 식별자로 보이는 속성이 관찰되어 이를 **현재
관찰 기준의 우선 후보**로 둡니다. 다만 그 존재와 활용 가능 범위는 **Parser·구조 분석 결과와 교차
확인이 필요**하며, 확정은 팀원 1의 분석 결과를 따릅니다.

### Pattern E — Event History (Q13 Q14 Q15)

| 단계 | 내용 |
|---|---|
| Metadata Filter | 기업, 세부유형, 공시일 범위. **정정 여부 필터를 걸지 않습니다**. 체인 전체가 대상 |
| Structural Retrieval | 정정 블록의 연결 필드와 변경 내역 표, 본문의 관련 공시 표기를 사용. **문서 간 이동이 링크 기반**이라는 점이 다른 Pattern과 근본적으로 다름 |
| Search | 불필요. 단 문서 확정 단계에서 본문 대조 필요(Q14) |
| Fallback | 연결 대상 후보가 복수면 본문 필드로 대조. 그래도 미확정이면 **모호함을 명시하고 후보를 병기**. 루트가 코퍼스 밖이면 **부분 이력임을 명시**하고 확보된 구간만 제시. 없는 원공시를 추정하지 않습니다 |

### Pattern F — Answerability Gate (Q16 Q17 Q18)

| 단계 | 내용 |
|---|---|
| Metadata Filter | Q16은 필터 실행 후 **결과가 비어 있다는 사실 자체가 근거**. Q17·Q18은 필터 이전 단계에서 종료 |
| Structural Retrieval | 미사용 |
| Search | **수행하지 않습니다** |
| Fallback | 없음. 거부가 정상 출력 |

## Vector Search 필요성 정리

18개 대표 질문 분석 기준의 현재 판단이며, 도입 결정은 D7 Pending입니다.

| 구분 | 문항 |
|---|---|
| 필수 | 없음 |
| 조건부 fallback 후보 | Q04 |
| 수행하지 않음 | Q16 Q17 Q18 |
| 무관 | 나머지 14문항 |

Q05는 문서 선택 문제이고 Q09는 Section 경로로 위치를 지정할 수 있어, 이전 검토에서 fallback
후보로 두었던 두 문항을 제외했습니다.

구조 기반 탐색을 먼저 완성한 뒤 실패 사례를 근거로 Vector Search 도입 여부를 재판단하는 것을
권고합니다. 도입 시점은 [open_decisions.md](open_decisions.md)의 D7에서 다룹니다.

## 미결정 사항

- 정정 보고서가 존재하는 정기공시에서 어느 판본을 검색 대상으로 삼을지는 **Pending Team Decision**
  입니다([D5](open_decisions.md)). Q01·Q07·Q10의 정답이 이 결정에 따라 달라집니다.
- 기간이 지정되지 않은 질문의 기본 문서 선택 정책은 [D5-a](open_decisions.md) Pending입니다.
- 표준 계정 식별자를 검색 키로 사용할 수 있는지는 팀원 1의 분석 결과 확인이 필요합니다.
- Chunk 생성 정책이 확정되지 않아 Pattern B의 Section 내부 분할 단위는 아직 정의하지 않았습니다.
  `docs/data_contracts.md` Draft가 Chunk를 검색 단위로 두는 것과 본 문서가 구조 기반 탐색을
  우선하는 것 사이의 정합은 [open_decisions.md](open_decisions.md)의 DC7에서 다룹니다.
