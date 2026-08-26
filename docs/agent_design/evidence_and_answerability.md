# Fact·Evidence와 답변 가능성

18개 대표 질문을 기준으로 Fact와 Evidence의 책임을 나누고, 답변 가능·불가능 판정 체계를
정리합니다. **본 문서는 초안이며 확정 규범이 아닙니다.** 아래 상태·사유 코드 목록과 최소 요건은
팀 검토를 거쳐 확정합니다.

문서군 표기는 [query_plan.md](query_plan.md)의 대응을 따르고, 문서 내부 구조는 **Section**으로
표기합니다. Capability 이름은 [agent_patterns.md](agent_patterns.md)를 정본으로 사용합니다.

## Fact와 Evidence의 구분

| 구분 | 정의 | 담는 정보 |
|---|---|---|
| **Fact** | Agent가 계산·비교에 사용하는 구조화 정보 | 기업, 지표, 기간, 회계 기준, 값, 단위 |
| **Evidence** | Fact 또는 답변 주장이 원문 어디에서 나왔는지 | 문서 식별자, 문서 내 위치, 원문 발췌 |

Fact는 "무엇이 사실인가"를, Evidence는 "그것을 어디서 확인할 수 있는가"를 담당합니다. 두 구조를
합치면 계산 입력과 근거 추적이 뒤섞여 검증이 어려워집니다.

위 구성은 `docs/data_contracts.md` Draft의 `Fact`·`Evidence`와 필드가 일치하지 않습니다. 차이는
[open_decisions.md](open_decisions.md)의 DC1~DC3에 기록했으며, 본 문서에서 Data Contract를
수정하지 않습니다.

## 핵심 원칙

1. **Fact는 자신의 출처 Evidence를 가집니다.** 출처가 없는 Fact는 계산에 사용하지 않습니다.
2. **값과 단위는 같은 출처에서 취합니다.** 한 문서 안에서도 Section에 따라 같은 지표가 다른 단위로
   표기되는 경우가 관찰되었습니다. 다른 Section의 단위를 빌려오지 않습니다.
3. **계산 결과는 입력 Fact를 참조합니다.** 어떤 Fact로 계산했는지 추적할 수 없는 결과는 검증할 수
   없습니다.
4. **비교 질문은 기업·기간별 Evidence를 각각 유지합니다.** 두 값을 하나의 근거로 묶지 않습니다.
5. **집계 질문은 합계에 포함된 각 항목의 Evidence를 유지합니다.** 합계만 제시하면 중복·누락을
   확인할 수 없습니다.
6. **Event History는 체인 전체의 Evidence를 유지합니다.** 일부만 제시하면 이력의 완결성을 확인할
   수 없습니다.
7. **거부 응답에는 Evidence를 붙이지 않습니다.** 답하지 않기로 한 판단에 원문 근거를 붙이면 부분
   답변으로 오인됩니다.

## 유형별 최소 요건 (현재 Draft 권고값)

아래 건수는 **팀원 2 권고값**이며 최종 요건이 아닙니다. 특히 시계열·기업 간 비교의 근거 문서
건수는 [open_decisions.md](open_decisions.md)의 **D4 Pending**에 직접 연결됩니다.

| 유형 | 최소 Fact | Evidence (Recommended) | 최종 요건 | 계산의 Fact 참조 |
|---|---|---|---|---|
| 1 단일 사실 (Q01 Q03) | 1 | 1 | 확정 | 계산 없음 |
| 1 단일 사실 (Q02) | 3 (금액, 시작일, 종료일) | 같은 문서 내 2~3 | 확정 | 계산 없음 |
| 2 단일 문서 요약 (Q04~Q06) | 0 (Fact 추출 아님) | 요약 항목 수만큼 | 확정 | 계산 없음. 원문 기재 비율은 인용하고 재계산하지 않음 |
| 3 시계열 수치 (Q07) | 2 | **2** | **Pending D4** | 입력 Fact 2건 전부 참조 |
| 3 시계열 파생지표 (Q08) | 4 | **2~4** | **Pending D4** | 기간별 분자·분모 4건 참조. **Calculation 3** |
| 3 시계열 서술 (Q09) | 0 | **2** (연도별 각 1) | **Pending D4** | 변화 항목마다 해당 연도 Evidence |
| 4 기업 간 수치 (Q10) | 2 | 2 (기업별 1) | 확정 | **Calculation 0**. Fact 2건을 Comparison Judgment로 판정해 ComparisonResult 1건. 정규화 전 값과 정규화 후 값을 모두 보존 |
| 4 기업 간 파생지표 (Q11) | 4 | 2~4 | 확정 | 기업별 비율 **Calculation 2** → Comparison Judgment → ComparisonResult 1건 |
| 4 기업 간 집계 (Q12) | 기업별 투자 건 수만큼 (합계 3 이상) | **합산에 포함된 투자 건별 1 이상**. 값을 확정한 정정공시가 있으면 그 문서도 포함 | 확정 | 기업별 합계 **Calculation 2** → Comparison Judgment → ComparisonResult 1건. 합계는 해당 기업의 건별 Fact 전부 참조 |
| 5 단일 필드 정정 (Q13) | 2 (변경 전·후) | 2 이상 (원공시와 정정공시) | 확정 | 변경점은 두 Fact 모두 참조 |
| 5 최종 상태 (Q14) | 3 이상 | 3 이상 (체인 전 문서) | 확정 | 상태 판정은 체인 전체 참조 |
| 5 다중 변경 (Q15) | 4 이상 | 체인 전 문서 | 확정 | 필드별 궤적은 해당 필드가 등장한 모든 문서 참조 |
| 6 답변 불가능 (Q16~Q18) | 0 | 0 | 확정 | 계산 없음 |

## Calculation cardinality

Calculation은 Fact 또는 다른 Calculation에서 나온 **수치형 파생 결과**만 가리킵니다. 관계 판정은
Calculation이 아니라 별도의 **ComparisonResult**입니다. 두 구조의 범위 정의는
[agent_patterns.md](agent_patterns.md#c10-calculation의-범위)를 정본으로 씁니다. 개수는 내부 산술
primitive 개수가 아니라 **최종 답변에서 의미 있게 사용되는 수치형 파생 결과 하나당 1건**으로
셉니다.

| 문항 | Calculation | ComparisonResult | 구성 | 근거 |
|---|---|---|---|---|
| Q08 | **3** | 없음 | 2024년 영업이익률 · 2025년 영업이익률 · 두 영업이익률의 변화량 | 원문에 없는 파생지표를 연도별로 각각 만들고, 질문이 "어떻게 달라졌어"로 변화량 자체를 요구합니다. 최종 우열 판정을 요구하지 않아 ComparisonResult가 없습니다 |
| Q10 | **0** | 1 | – | 원문 Fact 두 개를 그대로 비교하므로 수치형 파생 결과가 없습니다 |
| Q11 | **2** | 1 | 기업 A 영업이익률 · 기업 B 영업이익률 | 기업별 영업이익률 2건이 파생값입니다 |
| Q12 | **2** | 1 | 기업 A 투자 합계 · 기업 B 투자 합계 | 기업별 투자 합계 2건이 파생값입니다 |

- 최종 greater/less/equal 판정은 **Calculation count에 포함하지 않습니다.** Q10·Q11·Q12의 우열
  판정은 ComparisonResult로 표현합니다.
- Q10의 `0`은 계산 능력이 없다는 뜻이 아니라 **현재 질문이 수치형 파생 결과를 요구하지 않는다**는
  뜻입니다. 같은 두 Fact를 두고 "얼마나 더 큰가", "몇 %p 차이인가"처럼 차이 수치 자체를 물으면 그
  결과가 Calculation 1건으로 추가됩니다.
- Q07은 위 canonical 목록에 포함되지 않았습니다. 질문("얼마나 늘었어")이 증감 수치를 요구하므로
  Calculation은 최소 1건(증감액)이며, 답변에 증감률을 함께 제시하면 증감액·증감률 2건입니다.
  Q07·Q08 모두 최종 우열 판정을 요구하지 않으므로 ComparisonResult를 만들지 않습니다.
- **Comparability Validation이 실패하면** 그 지점 이후의 파생값도 ComparisonResult도 만들지 않고
  answerability 경로로 넘깁니다. 검증은 단일 단계가 아니라 연산 직전마다 적용되는 guard입니다
  (→ [agent_patterns.md](agent_patterns.md#comparability-validation의-적용-지점),
  [답변 가능성 상태](#답변-가능성-상태)).
- 여기서는 개수의 의미만 정합니다. 평가에서 이 개수를 어떻게 채점할지는 `evaluation/`의 범위입니다.

## 표기 예시

값은 형태 예시이며 실제 수치가 아닙니다.

**Q07 — 시계열 비교** (Evidence 건수는 D4 Pending)

```text
Fact A = {기업, 지표=revenue, 기간=직전 회계연도, 기준=연결, 값, 단위}
Fact B = {기업, 지표=revenue, 기간=당해 회계연도, 기준=연결, 값, 단위}

Validation①  = 동일 개념 ∧ 동일 기준 ∧ 단위 정규화 성공   # Calculation 입력 검증
Validation②  = 동일 기업 ∧ 두 기간 값의 비교 가능성        # 증감 산출 직전
Calculation  = delta(A, B), delta_pct(A, B)   # Python
Evidence A   = {직전 연도 사업보고서, 재무제표 Section, 해당 계정 행·열}
Evidence B   = {당해 연도 사업보고서, 재무제표 Section, 해당 계정 행·열}
```

**Q10 — 기업 간 비교 (단위 표기가 다른 경우)**

```text
Fact A = {기업 A, 지표=revenue, 기간, 기준=연결, 값, 단위 = 단위 X}
Fact B = {기업 B, 지표=revenue, 기간, 기준=연결, 값, 단위 = 단위 Y}

Validation②  = ... ∧ 단위 정규화 성공   # Calculation이 없어 비교 직전 guard만 적용
Calculation  = 없음 (질문이 수치형 파생 결과를 요구하지 않음)
Comparison   = ComparisonResult{left=Fact A, right=Fact B, relation}   # Comparison Judgment
출력          = 원문 단위 값과 정규화 값을 병기
                (정규화 결과만 제시하면 사용자가 검증할 수 없음)
```

**Q12 — 기업 간 집계 비교**

```text
기업 A 투자 건 = [건 A1, 건 A2, ...]      # 최초 공시가 대상 연도인 건만
기업 B 투자 건 = [건 B1, ...]

건별 Fact = {기업, 투자 건 식별, 금액, 단위, 최초 공시일}
            값은 해당 건의 필드 단위 최신값 (정정 반영)
            같은 건의 정정공시를 별개 건으로 세지 않음

Validation① = 단위 정규화 성공 ∧ 집계 대상 집합 확정 ∧ 건별 최신값 확정   # 집계 직전
Calculation = aggregate(기업 A 건별 Fact) , aggregate(기업 B 건별 Fact)   # 수치형 파생 2건
Validation② = 결산월 일치 ∧ 계정 체계 호환                                # 비교 직전
Comparison  = ComparisonResult{left=합계 A, right=합계 B, relation}       # Comparison Judgment

Evidence    = 건별 원공시 + 값을 확정한 정정공시
출력         = 기업별 합계와 함께 **합산에 포함된 투자 건 목록**을 제시
```

**Q15 — 정정 체인**

```text
Fact chain(투자금액)   = [원본, 정정 1, 정정 2, ...]
Fact chain(투자 종료일) = [원본, 정정 2, 정정 3, ...]

State resolution = 공시일 시간순 정렬 → 필드별 최신값
                   (수치형 파생값이 아니므로 Calculation으로 세지 않음)
Evidence    = 원본과 정정 공시 전량
              (일부 생략 시 이력 신뢰성을 확인할 수 없음)
```

## 답변 가능성 상태

| 상태 | 의미 | 해당 문항 |
|---|---|---|
| `ANSWERABLE` | 요구 정보를 확보했고 Evidence가 완비됨 | Q01~Q13, Q15 |
| `PARTIALLY_ANSWERABLE` | 일부만 확보. 확보분은 답하고 미확보분을 명시 | Q14의 일부, 체인 미완결 사례, Q12에서 일부 건의 최신값 미확정 시 |
| `UNANSWERABLE` | 답변 불가 | Q16 Q17 Q18 |

**Comparability Validation 실패** — 비교 가능성 1~8항은 각각 필요한 연산 직전에 확인합니다
(→ [agent_patterns.md](agent_patterns.md#comparability-validation의-적용-지점)). Calculation 입력
검증이 실패하면 Numeric Calculation을 수행하지 않고, 비교 가능성 검증이 실패하면 Comparison
Judgment를 성공 결과로 확정하지 않고 ComparisonResult를 생성하지 않으며, 두 경우 모두 위
answerability 경로로 넘깁니다.
실패를 `relation` 값으로 표현하지 않습니다. 어떤 `AnswerabilityStatus`·`AnswerabilityReason`·
stage/detail에 매핑할지는 **별도 Contract PR에서 확정**합니다.

## 사유 코드

아래 목록은 18문항에서 도출한 최소 집합이며 **확정본이 아닙니다**.

| 코드 | 의미 | 대표 문항 | 판정 근거 |
|---|---|---|---|
| `OUT_OF_DATA_RANGE` | 요구 기간이 보유 범위 밖 | Q16 | 코퍼스 메타데이터의 기간 경계와 대조 |
| `OUT_OF_SCOPE_DATA` | 요구하는 데이터 종류가 코퍼스에 없음 | Q17 | 보유 문서군 목록과 대조 |
| `OUT_OF_SCOPE_POLICY` | 정책상 지원하지 않음 | Q18 | `docs/mvp_scope.md` 제외 항목 |
| `WITHHELD_IN_SOURCE` | 원문에 항목은 있으나 공시유보로 미기재 | Q14, Q02 계열 | 필드값이 비어 있고 유보 사유가 기재됨 |
| `AMBIGUOUS_LINK` | 정정 대상 문서를 1건으로 확정할 수 없음 | Q14 | 연결 대상 후보가 복수 |
| `INCOMPLETE_CHAIN` | 체인의 루트가 보유 범위 밖 | Q15 유사 사례, Q12 집계 시 | 역참조 대상이 코퍼스 기간 이전 |
| `NOT_COMPARABLE` | 비교 전제 불충족 | Q10 실패 시 | 기준·단위·개념·계정 체계 검증 실패 |
| `NOT_FOUND` | 문서는 있으나 해당 정보를 찾지 못함 | 전 문항 공통 | 아래 절 참조 |
| `INSUFFICIENT_EVIDENCE` | 값은 얻었으나 Evidence 연결 실패 | 전 문항 공통 | 답변을 차단 |

## `NOT_FOUND`와 `OUT_OF_DATA_RANGE`의 구분

두 코드를 구분하는 것이 이 문서의 핵심입니다. **판정을 검색 결과가 아니라 메타데이터 대조에서
내리는 것**이 구분의 근거입니다.

```text
질문 → QueryPlan
  ↓
[1] 정책 판정        → 위반 시 OUT_OF_SCOPE_POLICY   (문서 접근 없음)
  ↓
[2] 데이터군 판정     → 보유 문서군에 없으면 OUT_OF_SCOPE_DATA
  ↓
[3] 범위 판정        → 코퍼스 커버리지와 대조
      문서가 존재해야 하는가?
      ├ 존재할 수 없음  → OUT_OF_DATA_RANGE      ← 데이터 부재 (확정)
      └ 존재해야 함
           ↓
[4] 문서 조회
      ├ 문서 없음     → OUT_OF_DATA_RANGE       ← 데이터 부재 (확정)
      └ 문서 있음
           ↓
[5] Section·Fact 탐색
      ├ 못 찾음      → NOT_FOUND                ← 검색 실패 (미확정)
      └ 찾음         → ANSWERABLE
```

- **3·4단계**는 문서 목록이라는 완전한 집합과 대조하므로 "없다"를 **확정**할 수 있습니다.
- **5단계**는 문서가 있는데 찾지 못한 것이므로 **검색 품질 문제일 수 있어 확정할 수 없습니다**.

따라서 `NOT_FOUND`는 `UNANSWERABLE`로 처리하지 않고 별도로 다룹니다. 사용자에게 전달할 때에도
표현을 구분합니다. `NOT_FOUND`는 해당 정보를 찾지 못했다는 표현으로 시스템 한계 가능성을 남기고,
`OUT_OF_DATA_RANGE`는 해당 기간 데이터를 보유하고 있지 않다는 확정적 표현을 사용합니다.

**평가 관점에서는 두 코드를 별도로 집계하는 것을 제안합니다.** 원인이 다른 실패를 한 지표로 묶으면
검색 품질 문제와 판정 능력 문제가 구분되지 않기 때문입니다. 실제 평가 지표의 정의와 산출 방식은
`docs/evaluation/`의 평가 설계에서 확정합니다.

## 설계 시 주의점

- **기간 밖 질문을 과거 방향으로 만들면 성립하지 않습니다.** 사업보고서 요약재무정보 표가 직전
  연도와 그 전년도를 비교 열로 함께 담고 있는 것으로 관찰되어, 수집 시작 연도 이전 값도 사실상
  조회 가능합니다. Q16을 최신 기간 이후 방향으로 설계한 이유입니다.
- **공시유보는 데이터 부재가 아닙니다.** 항목 자체는 존재하고 값만 비공개인 상태이므로
  `WITHHELD_IN_SOURCE`로 별도 구분합니다. 이 값을 다른 문서에서 추정해 채우지 않습니다.
- **체인 미완결과 연결 모호는 서로 다릅니다.** 전자는 문서가 코퍼스 밖에 있는 것이고, 후자는 코퍼스
  안에 있으나 어느 것인지 특정하지 못하는 것입니다.
- **집계에서 제외한 항목은 반드시 드러내야 합니다.** Q12처럼 여러 건을 합산할 때 최신값을 확정하지
  못한 건이 있으면 임의 값으로 채우지 않고 제외 사실과 사유를 함께 제시합니다.
- **지분공시(Q03)에는 개인 식별정보가 포함될 수 있습니다.** Gold·Evidence 작성 시 개인 식별정보가
  포함되지 않아야 하며, Agent 응답 단계의 처리 수준은 **팀 정책 확인이 필요합니다**
  ([open_decisions.md](open_decisions.md) D6).

## 미결정 사항

- 시계열 질문의 근거 문서 건수 요건은 [open_decisions.md](open_decisions.md)의 D4 Pending입니다.
- `docs/data_contracts.md` Draft와 본 문서의 Fact·Evidence 구성 차이는 DC1~DC3에 기록했습니다.
- 답변 가능성 상태와 사유 코드를 응답 구조에 담을지는 DC6 Pending입니다.
- `PARTIALLY_ANSWERABLE`의 부분 정답 채점 기준은 팀원 3의 평가 설계와 함께 확정해야 합니다.
