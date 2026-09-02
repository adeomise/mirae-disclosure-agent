"""근거 payload 조립 모듈.

역할:
- 확정된 실행 결과를 외부 모델이 읽을 구조로 옮긴다.

공개 경계:
    AgentExecutionResult -> build_grounded_payload -> GroundedPayload

    판정하지 않는다. 검색·계산·비교·답변 가능성은 이미 끝났고, 여기서는 그 결과를 외부
    모델이 읽을 표현으로 옮기기만 한다. answer_builder가 결정론 문장에 대해 하는 일과 같은
    자리이며, 다른 점은 결과가 문장이 아니라 구조라는 것뿐이다.

    status/reason을 다시 정하지 않는다. 실행 결과에 적힌 값을 그대로 싣고, 그 값이 무엇을
    뜻하는지는 producer가 정한다.

결정론 경로가 답하지 못하는 모양은 여기서도 답하지 않는다:
    build_answer_response가 거부하는 결과 조합(Fact와 EventHistory 동시, 비교 없는 다중
    Fact 등)은 build_grounded_payload도 거부한다. 한쪽만 통과시키면 연동을 켰을 때에만
    답해지는 결과가 생기고, 같은 질문이 설정에 따라 다르게 처리된다. 연동은 표현을 바꾸는
    일이지 답할 수 있는 범위를 넓히는 일이 아니다.

    그래서 일부 carrier만 골라 보내지 않는다. 표현할 수 없는 carrier가 섞여 있으면 나머지를
    보내는 대신 멈춘다. 남은 것만 보내면 빠진 쪽이 답변 어디에도 남지 않는다.

표기(대응표)의 정본은 answer_builder다:
    지표·단위·파생지표 이름과 값의 렌더링을 여기서 다시 만들지 않는다. 만들면 같은 Fact가
    결정론 경로와 HCX 경로에서 다른 이름·다른 자릿수로 불리고, 어느 쪽이 정본인지 알 수
    없게 된다.

    다만 문장 구절을 가져오지는 않는다. _period_phrase("2025년 ")나 _scope_phrase("연결 기준 ")는
    문장 안의 자리를 전제한 조각이라, payload가 쓰려면 공백과 '기준'을 잘라내야 한다. 그
    가공은 문장 조립 규칙을 payload 계층으로 옮겨 오는 것이므로 하지 않는다. 대신 그 구절이
    읽는 공개 대응표(METRIC_LABELS / ACCOUNTING_SCOPE_LABELS)를 직접 읽는다.

    대응표에 없는 값은 여기서 채우지 않고 PayloadNotGroundableError로 멈춘다. 표기를 이
    파일에서 만들면 "표기만 추가하지 말고 문장 템플릿을 먼저 정하라"는 규칙이 우회된다.

무엇을 싣지 않는가:
    Evidence.raw_text와 provenance는 넣지 않는다. 두 가지 이유다.
        1. 원문을 주면 모델이 거기서 새 사실을 뽑을 표면이 생긴다. 무엇을 근거로 삼을지는
           이미 확정됐고, 모델은 확정된 것을 옮기기만 한다.
        2. payload는 외부로 나간다. 최소 전송이 hyperclova 모듈의 정책이다.
    실리는 것은 evidence_id와 doc_id뿐이며, 그 목록은 검증 단계의 화이트리스트가 된다.

    answerability_detail·execution_issue·warnings·conversion_gaps도 넣지 않는다. 진단용
    내부 서술이고 사용자 문장의 재료가 아니다. 넘기면 모델이 그것을 근거로 삼아 답변에
    내부 사정을 적는다.

입력:
- AgentExecutionResult(COMPLETED, ANSWERABLE 또는 PARTIALLY_ANSWERABLE), 질문 문자열

출력:
- GroundedPayload

TODO:
- DocumentSummary(Pattern B) 지원. 현재는 요약 결과를 만나면 멈추고 결정론 경로에 맡긴다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from disclosure_agent.agent.answer_builder import (
    ACCOUNTING_SCOPE_LABELS,
    FULL_YEAR_FISCAL_PERIODS,
    LIMITATION_MESSAGE_BY_REASON,
    LIMITATION_WITHOUT_REASON_MESSAGE,
    METRIC_LABELS,
    SCOPE_OMITTED_IN_ANSWER,
    AnswerBuildError,
    _calculation_value,
    _derived_metric_label,
    _fact_value,
    _normalized_value_with_unit,
)
from disclosure_agent.common.enums import AnswerabilityStatus, ResultInputType
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    CalculationResult,
    Fact,
)

# 외부 모델이 표현할 판정 상태. 거부·미해결 문장은 결정론 대응표가 정본이다.
VERBALIZABLE_STATUSES: frozenset[AnswerabilityStatus] = frozenset(
    {AnswerabilityStatus.ANSWERABLE, AnswerabilityStatus.PARTIALLY_ANSWERABLE}
)


class PayloadNotGroundableError(RuntimeError):
    """근거 payload로 옮길 수 없는 결과를 받은 경우.

    호출부는 이것을 오류로 옮기지 않는다. "이 결과는 외부 모델에 맡기지 않는다"는 뜻이고,
    그 경우의 동작은 결정론 경로로 돌아가는 것이다.
    """


@dataclass(frozen=True)
class GroundedFact:
    """Fact 1건을 표기까지 확정한 형태.

    display_value는 answer_builder가 문장에 적는 것과 같은 문자열이다. 모델에게 숫자를
    포맷하게 하지 않는다. 자릿수 구분이나 반올림을 맡기는 순간 그것이 새 수치가 된다.

    fiscal_year는 정수 그대로 싣는다. "2025년"으로 미리 적지 않는 이유는 그 표기가 문장
    구절이기 때문이다(모듈 docstring 참조). 연도는 canonical 구조화 값이며 표기가 갈릴
    여지가 없다.

    scope_label이 None인 것은 '연결/별도를 확정하지 못했다'는 뜻이다. 임의로 연결을 적으면
    Q01의 주요 실패 모드를 payload 단계에서 다시 만든다.
    """

    fact_id: str
    company: str
    metric_label: str
    fiscal_year: int | None
    scope_label: str | None
    display_value: str
    normalized_display: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class GroundedCalculation:
    """계산 결과 1건.

    입력은 id로만 가리킨다. 입력 값을 함께 실으면 모델이 검산하거나 다시 계산할 표면이
    생긴다. 계산은 agent.calculator가 끝냈고 그 값이 정본이다.
    """

    calculation_id: str
    metric_label: str
    company: str
    fiscal_year: int | None
    scope_label: str | None
    display_value: str
    input_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class GroundedComparison:
    """우열 판정 1건. 관계 이름만 싣고 값을 다시 견주게 하지 않는다."""

    comparison_id: str
    left_id: str
    right_id: str
    relation: str


@dataclass(frozen=True)
class GroundedEventHistory:
    """이력 1건의 셈.

    변경 항목의 이름은 싣지 않는다. FieldChange.field_name은 canonical 영문 식별자이고
    production에서 raw_field는 비어 있다. 영문 이름을 넘기면 모델이 그것을 한국어로 옮기게
    되고, 그 번역이 추출 계층의 라벨 어휘와 다른 두 번째 대응표가 된다.

    변경 전후 값도 싣지 않는다. 그 값을 문장에 적는 형태는 결정론 경로에도 아직 없다
    (answer_builder의 Pattern E narrative TODO). 표현 방법이 정해지기 전에 모델에게 먼저
    주면, 그 문장이 사실상의 정본이 된다.
    """

    event_id: str
    document_count: int
    change_count: int
    chain_complete: bool


@dataclass(frozen=True)
class GroundedEvidence:
    """근거의 식별자. 원문은 싣지 않는다."""

    evidence_id: str
    doc_id: str


@dataclass(frozen=True)
class GroundedPayload:
    """외부 모델에 넘길 전부."""

    question: str
    answerability_status: str
    facts: tuple[GroundedFact, ...]
    calculations: tuple[GroundedCalculation, ...]
    comparisons: tuple[GroundedComparison, ...]
    event_histories: tuple[GroundedEventHistory, ...]
    evidence: tuple[GroundedEvidence, ...]
    limitation: str | None

    def to_json(self) -> str:
        """모델에게 실제로 보내는 문자열."""
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=False)

    def as_dict(self) -> dict[str, object]:
        """
        전송 형태.

        빈 carrier는 키 자체를 넣지 않는다. 빈 목록을 보내면 "확인했는데 없었다"로 읽히고,
        이 payload는 그런 주장을 담지 않는다.
        """
        body: dict[str, object] = {
            "question": self.question,
            "answerability_status": self.answerability_status,
        }

        if self.facts:
            body["facts"] = [
                {
                    "fact_id": fact.fact_id,
                    "company": fact.company,
                    "metric": fact.metric_label,
                    **({"fiscal_year": fact.fiscal_year} if fact.fiscal_year else {}),
                    **({"accounting_scope": fact.scope_label} if fact.scope_label else {}),
                    "value": fact.display_value,
                    **(
                        {"normalized_value": fact.normalized_display}
                        if fact.normalized_display
                        else {}
                    ),
                    "evidence_ids": list(fact.evidence_ids),
                }
                for fact in self.facts
            ]

        if self.calculations:
            body["calculations"] = [
                {
                    "calculation_id": calculation.calculation_id,
                    "company": calculation.company,
                    "metric": calculation.metric_label,
                    **({"fiscal_year": calculation.fiscal_year} if calculation.fiscal_year else {}),
                    **(
                        {"accounting_scope": calculation.scope_label}
                        if calculation.scope_label
                        else {}
                    ),
                    "value": calculation.display_value,
                    "input_ids": list(calculation.input_ids),
                    "evidence_ids": list(calculation.evidence_ids),
                }
                for calculation in self.calculations
            ]

        if self.comparisons:
            body["comparisons"] = [
                {
                    "comparison_id": comparison.comparison_id,
                    "left_id": comparison.left_id,
                    "right_id": comparison.right_id,
                    "relation": comparison.relation,
                }
                for comparison in self.comparisons
            ]

        if self.event_histories:
            body["event_histories"] = [
                {
                    "event_id": history.event_id,
                    "document_count": history.document_count,
                    "change_count": history.change_count,
                    "chain_complete": history.chain_complete,
                }
                for history in self.event_histories
            ]

        if self.evidence:
            body["evidence"] = [
                {"evidence_id": item.evidence_id, "doc_id": item.doc_id} for item in self.evidence
            ]

        if self.limitation:
            body["limitation"] = self.limitation

        return body


def build_grounded_payload(
    result: AgentExecutionResult,
    *,
    question: str,
) -> GroundedPayload:
    """
    확정된 실행 결과를 근거 payload로 옮긴다.

    Raises:
        PayloadNotGroundableError: 판정 상태가 외부 모델의 몫이 아니거나, 결정론 경로가
            표현하지 못하는 결과 조합이거나, 표기가 정해지지 않은 값이 섞여 있거나, 옮길
            근거가 하나도 없는 경우.
    """
    status = result.answerability_status

    if status not in VERBALIZABLE_STATUSES:
        raise PayloadNotGroundableError(
            f"외부 모델이 표현할 판정 상태가 아닙니다: {status!r}. "
            "거부·미해결 문장은 결정론 대응표가 정본입니다."
        )

    _require_supported_shape(result)

    try:
        facts = tuple(_ground_fact(fact) for fact in result.facts)

        calculations = tuple(
            _ground_calculation(calculation, result.facts) for calculation in result.calculations
        )

    except AnswerBuildError as error:
        # 표기가 정해지지 않은 값이다. 여기서 generic 표기를 만들면 대응표가 두 벌이 된다.
        raise PayloadNotGroundableError(str(error)) from error

    comparisons = tuple(
        GroundedComparison(
            comparison_id=comparison.comparison_id,
            left_id=comparison.left.input_id,
            right_id=comparison.right.input_id,
            relation=comparison.relation.value,
        )
        for comparison in result.comparisons
    )

    histories = tuple(
        GroundedEventHistory(
            event_id=history.event_id,
            document_count=len(history.document_ids),
            change_count=len(history.changes),
            chain_complete=history.chain_complete,
        )
        for history in result.event_histories
    )

    _require_known_comparison_operands(comparisons, facts, calculations)

    return GroundedPayload(
        question=question.strip(),
        answerability_status=status.value,
        facts=facts,
        calculations=calculations,
        comparisons=comparisons,
        event_histories=histories,
        evidence=tuple(
            GroundedEvidence(evidence_id=item.evidence_id, doc_id=item.doc_id)
            for item in result.evidence
        ),
        limitation=_limitation(result, status),
    )


def _require_supported_shape(result: AgentExecutionResult) -> None:
    """
    결정론 경로가 답하는 결과 모양인지 확인한다.

    각 줄의 근거는 answer_builder._supported_answer의 같은 분기다. 규칙을 새로 만들지 않고
    그쪽이 이미 거부하는 조합을 여기서도 거부한다. 한쪽만 통과시키면 연동을 켰을 때에만
    답해지는 결과가 생긴다.

    DocumentSummary는 그중에서도 이번 범위 밖이다. 결정론 경로는 Pattern B를 답하지만
    payload에는 요약을 옮길 자리가 없다. 요약을 빼고 나머지를 보내면 답의 본체가 사라지므로,
    요약이 실린 결과는 통째로 결정론 경로에 맡긴다.
    """
    if result.document_summaries:
        raise PayloadNotGroundableError(
            f"DocumentSummary {len(result.document_summaries)}건이 실려 있습니다. "
            "문서 요약은 아직 payload로 옮기지 않으며, 요약을 뺀 나머지만 보내지 않습니다."
        )

    if result.comparisons and result.event_histories:
        raise PayloadNotGroundableError(
            f"ComparisonResult {len(result.comparisons)}건과 "
            f"EventHistory {len(result.event_histories)}건이 함께 있습니다. "
            "두 결과를 함께 표현하는 형태가 정해지지 않았습니다."
        )

    if result.facts and result.event_histories:
        raise PayloadNotGroundableError(
            f"Fact {len(result.facts)}건과 EventHistory {len(result.event_histories)}건이 "
            "함께 있습니다. 두 결과를 함께 표현하는 형태가 정해지지 않았습니다."
        )

    if len(result.facts) > 1 and not result.comparisons:
        raise PayloadNotGroundableError(
            f"우열 판정 없이 Fact가 {len(result.facts)}건입니다. 결정론 경로도 이 모양을 "
            "문장으로 옮기지 않습니다(_single_fact_sentence)."
        )

    if not (result.facts or result.calculations or result.event_histories):
        raise PayloadNotGroundableError(
            "옮길 근거가 없습니다. facts/calculations/event_histories가 모두 비어 있습니다."
        )


def _ground_fact(fact: Fact) -> GroundedFact:
    """Fact 1건의 표기를 결정론 대응표로 확정한다."""
    return GroundedFact(
        fact_id=fact.fact_id,
        company=fact.company,
        metric_label=_metric_label(fact),
        fiscal_year=_fiscal_year(fact),
        scope_label=_scope_label(fact),
        display_value=_fact_value(fact),
        normalized_display=_normalized_value_with_unit(fact),
        evidence_ids=tuple(fact.evidence_ids),
    )


def _metric_label(fact: Fact) -> str:
    """
    지표 표기. 대응표는 answer_builder.METRIC_LABELS가 정본이다.

    없으면 멈춘다. generic 표기를 붙이면 지원하지 않는 지표가 payload를 통해 답변에 들어오고,
    결정론 경로가 같은 Fact에서 멈춘다는 사실이 가려진다.
    """
    label = METRIC_LABELS.get(fact.metric)

    if label is None:
        raise PayloadNotGroundableError(
            f"표기가 정해지지 않은 metric입니다: {fact.metric.value!r}. "
            "METRIC_LABELS에 없는 지표는 외부 모델에 넘기지 않습니다."
        )

    return label


def _scope_label(fact: Fact) -> str | None:
    """
    연결/별도 표기. 확정하지 못한 scope는 None이며, 그 자리를 채우지 않는다.

    _scope_phrase가 문장에서 구절을 생략하는 것과 같은 판정을 같은 공개 집합
    (SCOPE_OMITTED_IN_ANSWER)에서 읽는다.
    """
    if fact.accounting_scope in SCOPE_OMITTED_IN_ANSWER:
        return None

    label = ACCOUNTING_SCOPE_LABELS.get(fact.accounting_scope)

    if label is None:
        raise PayloadNotGroundableError(
            f"표기가 정해지지 않은 accounting_scope입니다: {fact.accounting_scope.value!r}."
        )

    return label


def _fiscal_year(fact: Fact) -> int | None:
    """
    회계연도. 연간 외 기간은 넘기지 않는다.

    _period_phrase가 문장에서 막는 것과 같은 규칙을 같은 공개 집합(FULL_YEAR_FISCAL_PERIODS)
    에서 읽는다. 분기 Fact를 연도만 실어 보내면 모델이 그것을 연간 값으로 적는다.
    """
    if fact.fiscal_period is not None and fact.fiscal_period not in FULL_YEAR_FISCAL_PERIODS:
        raise PayloadNotGroundableError(
            f"표기가 정해지지 않은 fiscal_period입니다: {fact.fiscal_period!r}. "
            "연간 외 기간은 외부 모델에 넘기지 않습니다."
        )

    return fact.fiscal_year


def _ground_calculation(
    calculation: CalculationResult,
    facts: list[Fact],
) -> GroundedCalculation:
    """
    계산 결과 1건의 표기를 확정한다.

    입력이 전부 Fact인 파생값만 옮긴다. 입력이 다른 Calculation인 값(기간 간 변화량 등)은
    개념 이름을 정할 대응표가 아직 없다. answer_builder._derived_metric_label이 그 대응표를
    agent.derived_metrics에서 읽고, 거기에는 원천 지표 조합만 등록돼 있다.

    그런 값에 "변화량" 같은 이름을 여기서 붙이지 않는다. 그러면 파생 개념 어휘가 결정론
    대응표 밖에 한 벌 더 생기고, 두 경로가 같은 계산을 다르게 부르게 된다. 이름을 정하는
    자리는 대응표이며, 정해지기 전까지 이 결과는 외부 모델에 넘기지 않는다.
    """
    if any(item.input_type is not ResultInputType.FACT for item in calculation.inputs):
        raise PayloadNotGroundableError(
            f"Calculation {calculation.calculation_id!r}의 입력에 다른 계산 결과가 있습니다. "
            "그 파생 개념의 표기가 대응표(agent.derived_metrics)에 아직 없어 "
            "외부 모델에 넘기지 않습니다."
        )

    input_ids = {item.input_id for item in calculation.inputs}

    sources = [fact for fact in facts if fact.fact_id in input_ids]

    if len(sources) != len(calculation.inputs):
        raise PayloadNotGroundableError(
            f"Calculation {calculation.calculation_id!r}의 입력 Fact를 결과 안에서 모두 찾지 "
            "못했습니다. 무엇에서 나온 값인지 확정할 수 없어 넘기지 않습니다."
        )

    companies = {fact.company for fact in sources}
    years = {_fiscal_year(fact) for fact in sources}
    scopes = {_scope_label(fact) for fact in sources}

    for attribute, values in (
        ("company", companies),
        ("fiscal_year", years),
        ("accounting_scope", scopes),
    ):
        if len(values) != 1:
            raise PayloadNotGroundableError(
                f"Calculation {calculation.calculation_id!r}의 입력 {attribute}가 한 값으로 "
                f"모이지 않습니다: {sorted(str(value) for value in values)}. "
                "어느 것의 값인지 정해지지 않았습니다."
            )

    return GroundedCalculation(
        calculation_id=calculation.calculation_id,
        metric_label=_derived_metric_label(calculation, sources),
        company=companies.pop(),
        fiscal_year=years.pop(),
        scope_label=scopes.pop(),
        display_value=_calculation_value(calculation),
        input_ids=tuple(item.input_id for item in calculation.inputs),
        evidence_ids=tuple(calculation.evidence_ids),
    )


def _require_known_comparison_operands(
    comparisons: tuple[GroundedComparison, ...],
    facts: tuple[GroundedFact, ...],
    calculations: tuple[GroundedCalculation, ...],
) -> None:
    """
    우열 판정이 가리키는 두 값이 payload 안에 있는지 확인한다.

    없으면 모델이 "무엇이 무엇보다 크다"를 쓸 대상을 payload 밖에서 찾아야 한다. 그 자리에서
    나오는 것은 추측이다.
    """
    known = {fact.fact_id for fact in facts} | {
        calculation.calculation_id for calculation in calculations
    }

    for comparison in comparisons:
        missing = {comparison.left_id, comparison.right_id} - known

        if missing:
            raise PayloadNotGroundableError(
                f"우열 판정 {comparison.comparison_id!r}이 payload에 없는 값을 가리킵니다: "
                f"{sorted(missing)}."
            )


def _limitation(result: AgentExecutionResult, status: AnswerabilityStatus) -> str | None:
    """
    일부만 답한 경우 붙는 한계 문장.

    문장을 모델이 짓게 하지 않고 결정론 대응표의 것을 그대로 넘긴다. 한계 진술은 "무엇을
    확인하지 못했는가"를 말하는 자리이고, 표현이 흔들리면 확인하지 못한 범위가 함께 흔들린다.
    """
    if status is not AnswerabilityStatus.PARTIALLY_ANSWERABLE:
        return None

    return LIMITATION_MESSAGE_BY_REASON.get(
        result.answerability_reason,
        LIMITATION_WITHOUT_REASON_MESSAGE,
    )
