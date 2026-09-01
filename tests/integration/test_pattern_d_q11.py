"""Pattern D (Cross-entity Compare) — 대표 질문 Q11 통합 테스트.

질문 문자열에서 공통 실행 Contract(AgentExecutionResult)까지의 흐름을 본다.

    QueryPlan
      -> 기업 A 원천 Fact -> guard① -> 영업이익률 Calculation
      -> 기업 B 원천 Fact -> guard① -> 영업이익률 Calculation
      -> guard② 기업 간 비교 가능성
      -> ComparisonResult

원천 Fact 확보를 대신 채우는 이유:
    영업이익은 ACODE 대응이 없어 본표 라벨 대조 경로로 읽힌다. 그 경로는 표 분류와 XBRL
    aclass까지 갖춘 parsed document를 요구하고, 현재 합성 fixture(synthetic_parsed_document)
    에는 그 표가 없다. 확보 흐름 자체는 tests/unit/test_period_retrieval.py가 실측 코퍼스까지
    포함해 이미 고정하고 있으므로, 여기서는 확보 이후의 조립을 본다.

    대신 어떤 조건으로 확보를 요청했는지는 검사한다(2절). 실제 코퍼스가 있으면 대신 채우지
    않고 끝까지 돌린다(6절).

무엇을 고정하는가:
    1. 파생지표 기업 간 비교 질문 하나가 Fact 4 / Calculation 2 / Comparison 1이 된다.
    2. 확보를 기업마다 따로 요청하고, 그 조건이 대응표와 계획에서 나온다.
    3. 한 기업이라도 확보하지 못하면 나머지 기업의 값도 남기지 않는다.
    4. 견줄 수 없는 두 기업은 파생값도 우열 판정도 남기지 않는다.

Pattern C(Q08)와 대비되는 자리:
    Q08은 Calculation 3건(연도별 비율 2 + 변화량 1)에 Comparison 0건이고, Q11은
    Calculation 2건(기업별 비율)에 Comparison 1건이다. 두 비율의 차이(%p)를 만들지 않는
    것이 Q11의 요건이며(evidence_and_answerability.md Calculation cardinality), 1절이 그것을
    개수와 연산 이름 양쪽으로 고정한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.answer_builder import (
    REASON_UNSUPPORTED_COMPARISON_METRIC,
    REASON_UNSUPPORTED_COMPARISON_PERIOD,
    REASON_UNSUPPORTED_COMPARISON_SUBJECT,
    STAGE_COMPARABILITY,
    STAGE_FILING_DISCOVERY,
    STAGE_PATTERN_ENTRY,
)
from disclosure_agent.agent.calculator import OPERATION_DIFF_PP, OPERATION_RATIO
from disclosure_agent.agent.derived_metrics import DerivedMetric
from disclosure_agent.agent.handlers import cross_entity_compare
from disclosure_agent.agent.handlers.cross_entity_compare import (
    build_cross_entity_compare_result,
    handle_cross_entity_compare,
)
from disclosure_agent.agent.period_retrieval import PeriodFactCollection
from disclosure_agent.agent.query_analyzer import (
    INTENT_CROSS_ENTITY_COMPARE,
    QueryPlan,
    build_query_plan,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ComparisonRelation,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import Evidence, Fact

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"

DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "3.공시" / "3.공시" / "corpus"
)

# 합성 manifest에 있는 두 기업. 둘 다 2025년 사업보고서를 가지고 있다.
LEFT_COMPANY = "테스트반도체(주)"

RIGHT_COMPANY = "테스트전자"

SYNTHETIC_QUESTION = (
    f"{LEFT_COMPANY}랑 {RIGHT_COMPANY} 중에 2025년 영업이익률이 더 높은 곳은 어디야?"
)

# 대표 질문 원문. evaluation/questions.jsonl의 표기와 같아야 한다.
Q11 = "HD현대중공업이랑 삼성중공업 중에 2025년 영업이익률이 더 높은 곳은 어디야?"

Q11_COMPANIES = ("HD현대중공업", "삼성중공업")

FISCAL_YEAR = 2025

# 합성 원천 값. 실제 공시 수치가 아니다.
#
# 테스트반도체: 456,789 / 1,234,567 -> 36.99993... -> 36.9999
# 테스트전자:   333,333 / 1,000,000 -> 33.3333
# 좌측이 더 크므로 left_greater다.
SOURCE_VALUES: dict[str, dict[MetricConcept, str]] = {
    LEFT_COMPANY: {
        MetricConcept.OPERATING_INCOME: "456789",
        MetricConcept.REVENUE: "1234567",
    },
    RIGHT_COMPANY: {
        MetricConcept.OPERATING_INCOME: "333333",
        MetricConcept.REVENUE: "1000000",
    },
}

EXPECTED_MARGINS = {LEFT_COMPANY: "36.9999", RIGHT_COMPANY: "33.3333"}


def _resolve_corpus_root() -> Path | None:
    raw = os.environ.get(CORPUS_ROOT_ENV)

    candidate = Path(raw) if raw else DEFAULT_CORPUS_ROOT

    if candidate.is_dir() and (candidate / "manifest.jsonl").is_file():
        return candidate

    return None


def _synthetic_fact(
    *,
    company: str,
    metric: MetricConcept,
    fiscal_year: int = FISCAL_YEAR,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
) -> tuple[Fact, Evidence]:
    """확보 계층이 돌려주는 형태의 Fact/Evidence 한 쌍."""
    doc_id = f"periodic_SYNTHETIC_{company}_{fiscal_year}"

    evidence_id = f"evidence_{doc_id}_{metric.value}"

    evidence = Evidence(
        evidence_id=evidence_id,
        doc_id=doc_id,
        location_type=EvidenceLocationType.TABLE,
        section_path=["III. 재무에 관한 사항", "2. 연결재무제표"],
        row_label=metric.value,
        raw_text=SOURCE_VALUES[company][metric],
    )

    fact = Fact(
        fact_id=f"fact_{doc_id}_{metric.value}",
        company=company,
        metric=metric,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
        raw_value=SOURCE_VALUES[company][metric],
        raw_unit="백만원",
        normalized_value=SOURCE_VALUES[company][metric],
        normalized_unit="원",
        evidence_ids=[evidence_id],
    )

    return fact, evidence


def _synthetic_collection(
    company: str,
    *,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
    fiscal_year: int = FISCAL_YEAR,
) -> PeriodFactCollection:
    """
    확보 계층이 기업 하나에 대한 Q11 요청에 돌려줄 결과.

    순서는 collect_period_facts와 같게 요청한 지표 순서(분자, 분모)다. 요청 순서를 바꾸지
    않는다는 것이 그쪽의 계약이므로 여기서도 지킨다.
    """
    facts: list[Fact] = []
    evidence: list[Evidence] = []

    for metric in (MetricConcept.OPERATING_INCOME, MetricConcept.REVENUE):
        fact, item = _synthetic_fact(
            company=company,
            metric=metric,
            fiscal_year=fiscal_year,
            accounting_scope=accounting_scope,
        )

        facts.append(fact)
        evidence.append(item)

    return PeriodFactCollection(facts=tuple(facts), evidence=tuple(evidence))


@pytest.fixture
def collected(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """
    확보 결과를 합성으로 대신 채우고, 어떤 조건으로 요청했는지 호출마다 기록한다.

    handler가 부르는 이름을 바꾼다. 확보 모듈 자체를 바꾸면 다른 Pattern까지 영향을 받는다.
    """
    calls: list[dict[str, object]] = []

    def _collect(**kwargs: object) -> PeriodFactCollection:
        calls.append(dict(kwargs))

        return _synthetic_collection(str(kwargs["company"]))

    monkeypatch.setattr(cross_entity_compare, "collect_period_facts", _collect)

    return calls


@pytest.fixture
def plan() -> QueryPlan:
    return build_query_plan(
        SYNTHETIC_QUESTION, known_companies=(LEFT_COMPANY, RIGHT_COMPANY)
    )


def _run(
    plan: QueryPlan,
    *,
    corpus_root: Path,
    manifest_path: Path | None = None,
):
    return handle_cross_entity_compare(
        plan,
        corpus_root=corpus_root,
        manifest_path=manifest_path,
    )


# --------------------------------------------------------------------------
# 1. Q11 모양 — Fact 4 / Calculation 2 / Comparison 1
# --------------------------------------------------------------------------


def test_question_is_planned_as_a_derived_metric_cross_entity_comparison(
    plan: QueryPlan,
) -> None:
    """전제 확인. 이 계획이 아니면 아래 검사가 다른 것을 재게 된다."""
    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.companies == (LEFT_COMPANY, RIGHT_COMPANY)
    assert plan.years == (FISCAL_YEAR,)
    assert plan.metric is None
    assert plan.derived_metric is DerivedMetric.OPERATING_MARGIN


def test_derived_metric_cross_entity_question_is_answerable(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert result.execution_issue is None


def test_result_carries_four_facts_two_calculations_and_one_comparison(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """
    개수는 evidence_and_answerability.md의 Calculation cardinality가 정한다.

    Comparison이 1인 것은 질문이 최종 우열 판정을 요구하기 때문이며, Q08이 변화량 자체를
    물어 Comparison 0인 것과 대비된다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    assert len(result.facts) == 4
    assert len(result.evidence) == 4
    assert len(result.calculations) == 2
    assert len(result.comparisons) == 1


def test_no_difference_calculation_is_produced(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """
    두 비율의 차이(%p)를 만들지 않는다.

    질문이 묻는 것은 "어디가 더 높은가"이고 차이 수치가 아니다. 묻지 않은 값을 만들면
    Calculation 개수가 3이 되어 Q08과 구분되지 않는다. 개수 검사만으로는 잡히지 않는
    경우가 있어(비율 하나가 빠지고 차이가 들어오는 경우) 연산 이름으로도 본다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    operations = [item.operation for item in result.calculations]

    assert operations == [OPERATION_RATIO, OPERATION_RATIO]
    assert OPERATION_DIFF_PP not in operations


def test_source_facts_cover_both_companies_and_both_metrics(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    result = _run(plan, corpus_root=nfd_corpus_root)

    assert {(fact.company, fact.metric) for fact in result.facts} == {
        (company, metric)
        for company in (LEFT_COMPANY, RIGHT_COMPANY)
        for metric in (MetricConcept.OPERATING_INCOME, MetricConcept.REVENUE)
    }

    assert {fact.fiscal_year for fact in result.facts} == {FISCAL_YEAR}


def test_calculations_are_two_percent_ratios_in_question_order(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """
    순서는 질문에 나온 기업 순서다.

    정렬하지 않는다. 정렬하면 "A랑 B 중에"라고 물은 질문에 B가 좌측인 판정이 돌아가고,
    관계 이름이 뒤집혀도 그럴듯하다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    left, right = result.calculations

    assert (left.unit, right.unit) == ("percent", "percent")
    assert left.value == EXPECTED_MARGINS[LEFT_COMPANY]
    assert right.value == EXPECTED_MARGINS[RIGHT_COMPANY]


def test_comparison_relation_follows_the_two_margins(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    result = _run(plan, corpus_root=nfd_corpus_root)

    (comparison,) = result.comparisons

    assert comparison.relation is ComparisonRelation.LEFT_GREATER


def test_comparison_inputs_point_at_the_two_calculations(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """
    Q11은 원천 Fact가 아니라 기업별 비율을 견준다.

    Fact를 가리키면 Q10의 모양이 되고, 그때는 파생값을 만든 의미가 사라진다
    (evidence_and_answerability.md: Q10은 Fact 2건, Q11은 Calculation 2건).
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    left, right = result.calculations

    (comparison,) = result.comparisons

    assert [
        (comparison.left.input_type, comparison.left.input_id),
        (comparison.right.input_type, comparison.right.input_id),
    ] == [
        (ResultInputType.CALCULATION, left.calculation_id),
        (ResultInputType.CALCULATION, right.calculation_id),
    ]


def test_each_margin_references_its_own_numerator_and_denominator(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """분자와 분모가 그 기업의 것이어야 한다. 섞이면 어느 기업의 비율도 아니다."""
    result = _run(plan, corpus_root=nfd_corpus_root)

    facts = {fact.fact_id: fact for fact in result.facts}

    for calculation, company in zip(
        result.calculations, (LEFT_COMPANY, RIGHT_COMPANY), strict=True
    ):
        numerator, denominator = (
            facts[reference.input_id] for reference in calculation.inputs
        )

        assert numerator.metric is MetricConcept.OPERATING_INCOME
        assert denominator.metric is MetricConcept.REVENUE
        assert numerator.company == company
        assert denominator.company == company


def test_every_result_input_reference_resolves_inside_the_same_response(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """
    참조가 결과 밖을 가리키면 받는 쪽이 무엇에서 나온 값인지 되짚을 수 없다.

    evaluation.runner의 result_input_references 검사와 같은 성질을 Calculation과 Comparison
    양쪽에서 본다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    known = {
        ResultInputType.FACT: {fact.fact_id for fact in result.facts},
        ResultInputType.CALCULATION: {
            item.calculation_id for item in result.calculations
        },
    }

    references = [
        reference
        for calculation in result.calculations
        for reference in calculation.inputs
    ] + [
        reference
        for comparison in result.comparisons
        for reference in (comparison.left, comparison.right)
    ]

    dangling = [
        (reference.input_type, reference.input_id)
        for reference in references
        if reference.input_id not in known[reference.input_type]
    ]

    assert dangling == []


def test_every_calculation_evidence_reference_resolves(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """
    기업별 비율은 그 기업의 근거만 들고 있어야 한다.

    두 기업의 근거가 한 Calculation에 섞이면 어느 문서에서 나온 값인지 알 수 없다.
    Pattern C의 변화량이 두 기간의 근거를 모두 드는 것과 다른 자리이며, 이유는 그쪽이
    두 값에서 나온 값이고 이쪽은 한 기업에서 나온 값이기 때문이다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    available = {item.evidence_id for item in result.evidence}

    for calculation in result.calculations:
        assert calculation.evidence_ids
        assert not set(calculation.evidence_ids) - available

    left, right = result.calculations

    assert set(left.evidence_ids).isdisjoint(right.evidence_ids)
    assert set(left.evidence_ids) | set(right.evidence_ids) == available


def test_result_identifiers_are_deterministic(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """같은 질문을 두 번 실행하면 같은 식별자가 나와야 한다."""
    first = _run(plan, corpus_root=nfd_corpus_root)

    second = _run(plan, corpus_root=nfd_corpus_root)

    calculations = [item.calculation_id for item in first.calculations]

    comparisons = [item.comparison_id for item in first.comparisons]

    assert calculations == [item.calculation_id for item in second.calculations]
    assert comparisons == [item.comparison_id for item in second.comparisons]

    assert calculations == [
        f"calculation_operating_margin_2025_{LEFT_COMPANY}",
        f"calculation_operating_margin_2025_{RIGHT_COMPANY}",
    ]

    assert comparisons == [
        f"comparison_operating_margin_2025_{LEFT_COMPANY}_vs_{RIGHT_COMPANY}"
    ]


def test_the_two_companies_do_not_share_a_calculation_identifier(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """
    식별자에 기업 축이 들어 있어야 한다.

    회계연도만 쓰면 두 기업의 비율이 같은 식별자를 갖고, Comparison의 두 참조가 같은 곳을
    가리키게 된다. Pattern C가 "기업 축이 필요한 Pattern D는 그 handler가 자기 축을
    덧붙인다"고 남겨 둔 자리다.
    """
    left, right = _run(plan, corpus_root=nfd_corpus_root).calculations

    assert left.calculation_id != right.calculation_id


# --------------------------------------------------------------------------
# 2. 확보 요청 조건 — 기업마다 따로, 대응표와 계획에서
# --------------------------------------------------------------------------


def test_retrieval_is_requested_once_per_company_in_question_order(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """
    확보 계층은 기업 하나를 받는다. 기업 축을 그쪽에 넣지 않는다.

    순서를 지키는 이유는 결과의 순서가 곧 left/right이기 때문이다.
    """
    _run(plan, corpus_root=nfd_corpus_root)

    assert [call["company"] for call in collected] == [LEFT_COMPANY, RIGHT_COMPANY]


def test_source_metrics_are_expanded_from_the_registry(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    """
    영업이익률을 그대로 확보하러 가지 않는다.

    파생 개념은 어느 문서에도 적혀 있지 않으므로, 확보 계층에 그대로 넘기면 지원하지 않는
    지표로 막힌다. 대응표가 분자와 분모로 펼친다. Pattern C와 같은 대응표다.
    """
    _run(plan, corpus_root=nfd_corpus_root)

    for call in collected:
        assert call["metrics"] == (
            MetricConcept.OPERATING_INCOME,
            MetricConcept.REVENUE,
        )


def test_retrieval_conditions_come_from_the_plan(
    plan: QueryPlan,
    collected: list[dict[str, object]],
    nfd_corpus_root: Path,
) -> None:
    _run(plan, corpus_root=nfd_corpus_root)

    for call in collected:
        assert call["fiscal_years"] == (FISCAL_YEAR,)
        assert call["accounting_scope"] == plan.accounting_scope
        assert call["document_class"] == plan.document_class
        assert call["document_subtype"] == plan.document_subtype


# --------------------------------------------------------------------------
# 3. 다루지 못하는 계획과 확보 실패
#
# 무엇 하나 만들지 않고 막혀야 하며, 한 기업의 값만 남으면 축소해서 답했다는 뜻이다.
# --------------------------------------------------------------------------


def _cross_entity_plan(**overrides) -> QueryPlan:
    """검증에 필요한 축만 바꾼 기업 간 비교 계획."""
    fields: dict[str, object] = {
        "question": "합성 질문",
        "companies": (LEFT_COMPANY, RIGHT_COMPANY),
        "years": (FISCAL_YEAR,),
        "intent": INTENT_CROSS_ENTITY_COMPARE,
        "metric": None,
        "derived_metric": DerivedMetric.OPERATING_MARGIN,
    }

    fields.update(overrides)

    return QueryPlan(**fields)


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        # 기업이 셋. 이번 구현은 둘만 견준다.
        (
            {"companies": (LEFT_COMPANY, RIGHT_COMPANY, "테스트해양")},
            REASON_UNSUPPORTED_COMPARISON_SUBJECT,
        ),
        # 같은 기업을 두 번 담은 계획. 견줄 것이 없다.
        (
            {"companies": (LEFT_COMPANY, LEFT_COMPANY)},
            REASON_UNSUPPORTED_COMPARISON_SUBJECT,
        ),
        # 기업 축과 기간 축이 함께 여럿이다.
        ({"years": (2024, 2025)}, REASON_UNSUPPORTED_COMPARISON_PERIOD),
        # 지표를 하나도 읽지 못한 질문. Q12가 지금 이 상태다.
        (
            {"metric": None, "derived_metric": None},
            REASON_UNSUPPORTED_COMPARISON_METRIC,
        ),
    ],
)
def test_unsupported_plans_are_blocked_at_the_pattern_entry(
    overrides: dict[str, object],
    expected_reason: str,
    nfd_corpus_root: Path,
    synthetic_manifest_path: Path,
) -> None:
    """
    코퍼스를 열기 전에 전부 판정한다.

    답변 가능성을 판정하지 않는다. 문서를 보지 않았으므로 답이 있는지 없는지 알지 못한다.
    """
    result = _run(
        _cross_entity_plan(**overrides),
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.answerability_reason is None
    assert result.facts == []
    assert result.evidence == []
    assert result.calculations == []
    assert result.comparisons == []

    issue = result.execution_issue

    assert issue is not None
    assert issue.stage == STAGE_PATTERN_ENTRY
    assert issue.reason == expected_reason


def test_a_duplicated_company_never_opens_the_corpus(
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """
    같은 기업을 두 번 담은 계획은 확보 이전에 막힌다.

    guard②도 같은 어긋남을 잡지만 그때는 이미 문서를 두 번 연 뒤이고, 같은 fact_id를 가진
    Fact가 두 벌 실린 결과가 만들어진 뒤다.
    """

    def _forbidden(**kwargs: object) -> PeriodFactCollection:
        raise AssertionError(f"확보를 시도했습니다: {sorted(kwargs)}")

    monkeypatch.setattr(cross_entity_compare, "collect_period_facts", _forbidden)

    result = _run(
        _cross_entity_plan(companies=(LEFT_COMPANY, LEFT_COMPANY)),
        corpus_root=nfd_corpus_root,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED


def test_a_missing_filing_for_one_company_drops_the_other_company_too(
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
    synthetic_manifest_path: Path,
) -> None:
    """
    한 기업만 확보된 결과를 남기지 않는다.

    A사 Fact가 결과에 남아 있으면 받는 쪽이 그것만으로 답을 조립할 수 있고, 그러면 두 기업을
    물은 질문에 한 기업만 답하는 결과가 된다. 그것이 F6-04에서 막은 동작이다.

    확보 계층의 정지 사유는 새로 해석하지 않는다. 합성 manifest에 없는 연도를 요청하면
    filing_discovery에서 막히며, 그 판정은 이미 normalization의 대응표가 정하고 있다.
    """
    result = _run(
        _cross_entity_plan(years=(2023,)),
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert result.facts == []
    assert result.evidence == []
    assert result.calculations == []
    assert result.comparisons == []

    detail = result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_FILING_DISCOVERY


def test_the_blocked_detail_names_which_company_was_missing(
    nfd_corpus_root: Path,
    synthetic_manifest_path: Path,
) -> None:
    """
    어느 기업에서 막혔는지가 결과에 남아야 후속 조치를 고를 수 있다.

    stage와 reason은 확보 계층이 내린 판정의 정본이므로 바꾸지 않고 detail에만 덧붙인다.
    """
    result = build_cross_entity_compare_result(
        _cross_entity_plan(years=(2023,)),
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.blocked is not None
    assert LEFT_COMPANY in result.blocked.detail


def test_the_second_company_is_not_fetched_after_the_first_one_fails(
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """
    첫 기업에서 막히면 나머지를 마저 조회하지 않는다.

    어차피 부분 결과로는 답하지 않으므로 문서를 더 여는 것은 비용만 늘린다. 확보 계층이
    (연도 x 지표)에 대해 이미 지키는 성질을 기업 축으로 넓힌 것이다.
    """
    from disclosure_agent.agent.answer_builder import (
        REASON_NO_MATCHING_FILING,
        BlockedInfo,
    )

    calls: list[str] = []

    def _collect(**kwargs: object) -> PeriodFactCollection:
        calls.append(str(kwargs["company"]))

        return PeriodFactCollection(
            blocked=BlockedInfo(
                stage=STAGE_FILING_DISCOVERY,
                reason=REASON_NO_MATCHING_FILING,
                detail="합성 정지",
            )
        )

    monkeypatch.setattr(cross_entity_compare, "collect_period_facts", _collect)

    _run(_cross_entity_plan(), corpus_root=nfd_corpus_root)

    assert calls == [LEFT_COMPANY]


# --------------------------------------------------------------------------
# 4. guard② — 견줄 수 없는 두 기업
#
# 아래 입력은 기업별 guard①을 모두 통과한다. 기업 사이의 어긋남은 guard②만 잡을 수 있다.
# --------------------------------------------------------------------------


def _mixed_scope_collection(**kwargs: object) -> PeriodFactCollection:
    """좌측은 연결, 우측은 별도로 확보된 결과."""
    company = str(kwargs["company"])

    scope = (
        FactAccountingScope.CONSOLIDATED
        if company == LEFT_COMPANY
        else FactAccountingScope.SEPARATE
    )

    return _synthetic_collection(company, accounting_scope=scope)


def test_scope_mismatch_across_companies_is_not_comparable(
    plan: QueryPlan,
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """
    A사 연결과 B사 별도는 각 기업의 guard①을 통과한다.

    기업 사이의 어긋남은 guard②만 잡을 수 있고, 놓치면 기준이 다른 두 비율의 우열이
    기업의 우열로 나간다.
    """
    monkeypatch.setattr(
        cross_entity_compare, "collect_period_facts", _mixed_scope_collection
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_COMPARABLE


def test_incomparable_result_keeps_the_facts_and_drops_every_derived_value(
    plan: QueryPlan,
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """
    확보는 정상이었으므로 Fact와 Evidence는 남는다.

    파생값과 우열 판정은 하나도 남기지 않는다. 기업별 비율 2건이 남아 있으면 받는 쪽이 그
    둘을 견줘 우열을 직접 정할 수 있고, 그러면 guard가 막은 일이 결과를 받는 쪽에서 그대로
    일어난다.
    """
    monkeypatch.setattr(
        cross_entity_compare, "collect_period_facts", _mixed_scope_collection
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert len(result.facts) == 4
    assert len(result.evidence) == 4
    assert result.calculations == []
    assert result.comparisons == []


def test_incomparable_result_records_which_guard_stopped_it(
    plan: QueryPlan,
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """
    canonical 사유 하나로는 어느 축이 어긋났는지 알 수 없다.

    계산 입력이 어긋난 것과 두 기간이 어긋난 것과 두 기업이 어긋난 것은 후속 조치가 다르므로
    원문을 남긴다. Pattern C가 incomparable_periods를 남기는 자리와 같다.
    """
    monkeypatch.setattr(
        cross_entity_compare, "collect_period_facts", _mixed_scope_collection
    )

    detail = _run(plan, corpus_root=nfd_corpus_root).answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_COMPARABILITY
    assert detail.reason == "incomparable_entities"
    assert detail.detail


def test_a_zero_denominator_stops_before_any_value_is_made(
    plan: QueryPlan,
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """
    guard①이 막는다. 기업 축이라고 달라지지 않는다.

    코퍼스에 대한 판정이 아니므로 답변 가능성으로 나가지 않고 실행 중단으로 남는다
    (normalization의 ANSWERABILITY_BY_STOP에 unusable_calculation_input이 없다).
    """

    def _zero_revenue(**kwargs: object) -> PeriodFactCollection:
        company = str(kwargs["company"])

        collection = _synthetic_collection(company)

        if company != RIGHT_COMPANY:
            return collection

        numerator, denominator = collection.facts

        return PeriodFactCollection(
            facts=(numerator, denominator.model_copy(update={"normalized_value": "0"})),
            evidence=collection.evidence,
        )

    monkeypatch.setattr(cross_entity_compare, "collect_period_facts", _zero_revenue)

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.calculations == []
    assert result.comparisons == []

    issue = result.execution_issue

    assert issue is not None
    assert issue.stage == STAGE_COMPARABILITY
    assert issue.reason == "unusable_calculation_input"


def test_an_unreadable_value_stops_before_any_value_is_made(
    plan: QueryPlan,
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """수로 읽지 못하는 값으로는 비율을 만들지 않는다."""

    def _unreadable(**kwargs: object) -> PeriodFactCollection:
        collection = _synthetic_collection(str(kwargs["company"]))

        numerator, denominator = collection.facts

        return PeriodFactCollection(
            facts=(
                numerator.model_copy(update={"normalized_value": "해당사항없음"}),
                denominator,
            ),
            evidence=collection.evidence,
        )

    monkeypatch.setattr(cross_entity_compare, "collect_period_facts", _unreadable)

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.calculations == []
    assert result.comparisons == []


def test_a_missing_source_metric_is_reported_instead_of_guessed(
    plan: QueryPlan,
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """
    확보 결과에 분자가 없으면 막는다.

    확보 계층은 요청한 조합을 전부 담거나 하나도 담지 않으므로 정상 경로에서는 일어나지
    않는다. 그래도 확인하는 이유는, 그 계약이 바뀌었을 때 사실이 값이 아니라 예외로
    드러나면 어느 조합이 빠졌는지 결과에 남지 않기 때문이다.
    """

    def _denominator_only(**kwargs: object) -> PeriodFactCollection:
        collection = _synthetic_collection(str(kwargs["company"]))

        return PeriodFactCollection(
            facts=(collection.facts[1],),
            evidence=(collection.evidence[1],),
        )

    monkeypatch.setattr(cross_entity_compare, "collect_period_facts", _denominator_only)

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.calculations == []
    assert result.comparisons == []

    issue = result.execution_issue

    assert issue is not None
    assert MetricConcept.OPERATING_INCOME.value in issue.detail


# --------------------------------------------------------------------------
# 5. Router 경유
# --------------------------------------------------------------------------


def test_router_dispatches_the_intent_to_this_handler() -> None:
    assert (
        router.resolve_handler(INTENT_CROSS_ENTITY_COMPARE)
        is handle_cross_entity_compare
    )


def test_question_string_reaches_the_same_result_through_the_router(
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
    synthetic_manifest_path: Path,
) -> None:
    """질문 문자열부터 실행 결과까지 한 번에 본다."""
    monkeypatch.setattr(
        cross_entity_compare,
        "collect_period_facts",
        lambda **kwargs: _synthetic_collection(str(kwargs["company"])),
    )

    result = router.answer_question(
        SYNTHETIC_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert len(result.facts) == 4
    assert len(result.calculations) == 2
    assert len(result.comparisons) == 1


# --------------------------------------------------------------------------
# 6. 실측 코퍼스 — 대표 질문 Q11
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_q11_is_answerable_in_the_actual_corpus() -> None:
    """
    대표 질문 원문을 그대로 실행한다. 확보를 대신 채우지 않는다.

    수치는 실제 공시 값에서 나오므로 저장소에 적지 않는다(AGENTS.md). 여기서는 개수와 단위,
    참조 정합만 본다. 값 자체는 tests/unit/test_cross_entity_comparability.py가 합성 입력으로
    고정한다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q11, corpus_root=corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None

    assert len(result.facts) == 4
    assert len(result.evidence) == 4
    assert len(result.calculations) == 2
    assert len(result.comparisons) == 1

    assert [item.operation for item in result.calculations] == [
        OPERATION_RATIO,
        OPERATION_RATIO,
    ]

    assert [item.unit for item in result.calculations] == ["percent", "percent"]


@pytest.mark.corpus
def test_q11_relation_is_one_of_the_three_successful_values() -> None:
    """
    ComparisonResult는 비교가 정상적으로 성공한 경우에만 존재한다.

    not_comparable·unknown 같은 값을 두지 않으므로, 판정이 나왔다면 셋 중 하나여야 한다
    (evidence_and_answerability.md Comparison Judgment).

    어느 기업이 더 높은지는 적지 않는다. 실제 공시 값에서 나오는 판정이기 때문이다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    (comparison,) = router.answer_question(Q11, corpus_root=corpus_root).comparisons

    assert comparison.relation in set(ComparisonRelation)


@pytest.mark.corpus
def test_q11_references_resolve_inside_the_actual_result() -> None:
    """
    Fact -> Evidence, Calculation -> Evidence, Calculation -> Fact, Comparison -> Calculation.

    evaluation.runner가 gold와 대조하는 정합을 같은 형태로 본다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q11, corpus_root=corpus_root)

    evidence_ids = {item.evidence_id for item in result.evidence}

    known = {
        ResultInputType.FACT: {fact.fact_id for fact in result.facts},
        ResultInputType.CALCULATION: {
            item.calculation_id for item in result.calculations
        },
    }

    for fact in result.facts:
        assert fact.evidence_ids
        assert not set(fact.evidence_ids) - evidence_ids

    for calculation in result.calculations:
        assert calculation.evidence_ids
        assert not set(calculation.evidence_ids) - evidence_ids
        assert calculation.inputs

        for reference in calculation.inputs:
            assert reference.input_id in known[reference.input_type]

    for comparison in result.comparisons:
        for reference in (comparison.left, comparison.right):
            assert reference.input_type is ResultInputType.CALCULATION
            assert reference.input_id in known[ResultInputType.CALCULATION]


@pytest.mark.corpus
def test_q11_source_facts_cover_two_companies_in_one_year_and_one_scope() -> None:
    """guard②가 실제로 통과할 조건이었는지 결과에서 확인한다."""
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q11, corpus_root=corpus_root)

    assert len({fact.company for fact in result.facts}) == 2
    assert {fact.fiscal_year for fact in result.facts} == {FISCAL_YEAR}
    assert {fact.accounting_scope for fact in result.facts} == {
        FactAccountingScope.CONSOLIDATED
    }

    assert {fact.metric for fact in result.facts} == {
        MetricConcept.OPERATING_INCOME,
        MetricConcept.REVENUE,
    }


@pytest.mark.corpus
def test_q11_calculation_identifiers_carry_the_planned_company_names() -> None:
    """
    식별자는 계획에 담긴 기업 표기로 만든다.

    Fact.company는 문서에 적힌 상호이며 manifest 표기와 다를 수 있다("HD현대중공업" ->
    "에이치디현대중공업(주)"). 식별자를 그쪽에서 만들면 같은 질문의 식별자가 확보 결과에
    매이고, 질문과 결과를 이어 볼 수 없다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q11, corpus_root=corpus_root)

    assert [item.calculation_id for item in result.calculations] == [
        f"calculation_operating_margin_2025_{company}" for company in Q11_COMPANIES
    ]

    (comparison,) = result.comparisons

    assert comparison.comparison_id == (
        f"comparison_operating_margin_2025_{Q11_COMPANIES[0]}_vs_{Q11_COMPANIES[1]}"
    )
