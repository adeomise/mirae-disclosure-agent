"""Pattern C (Temporal Compare) 통합 테스트.

질문 문자열에서 공통 실행 Contract(AgentExecutionResult)까지의 흐름을 본다.

원천 Fact 확보를 대신 채우는 이유:
    영업이익은 ACODE 대응이 없어 본표 라벨 대조 경로로 읽힌다. 그 경로는 표 분류와 XBRL
    aclass까지 갖춘 parsed document를 요구하고, 현재 합성 fixture(synthetic_parsed_document)
    에는 그 표가 없다. 확보 흐름 자체는 tests/unit/test_period_retrieval.py가 실측 코퍼스까지
    포함해 이미 고정하고 있으므로, 여기서는 확보 이후의 조립을 본다.

    대신 어떤 조건으로 확보를 요청했는지는 검사한다(2절). 이 handler가 대응표를 원천 지표로
    펼쳐 넘기는 것과 plan.years를 쓰는 것이 확보 계층과의 계약 전부다.

    실제 코퍼스가 있으면 대신 채우지 않고 끝까지 돌린다(6절).

무엇을 고정하는가:
    1. 파생지표 시계열 질문 하나가 Fact 4건 / Calculation 3건 / Comparison 0건이 된다.
    2. 확보 요청 조건이 대응표와 계획에서 나온다.
    3. 이번 구현이 다루지 못하는 시계열 계획은 값을 만들지 않고 막힌다.
    4. 견줄 수 없는 두 기간은 파생값을 하나도 남기지 않는다.

Router 등록과의 관계:
    handler가 등록되기 전에는 이 질문들이 routing/unimplemented_intent로 막혔다. 등록한
    뒤에도 막히는 것은 같고 자리만 pattern_entry로 옮겨온다. 그 성질은
    tests/unit/test_multi_period_guard.py가 함께 고정한다.
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
    STAGE_FILING_DISCOVERY,
    STAGE_PATTERN_ENTRY,
)
from disclosure_agent.agent.calculator import (
    OPERATION_DIFF_PP,
    OPERATION_RATIO,
    UNIT_PERCENTAGE_POINT,
)
from disclosure_agent.agent.derived_metrics import DerivedMetric
from disclosure_agent.agent.handlers import temporal_compare
from disclosure_agent.agent.handlers.temporal_compare import (
    build_temporal_compare_result,
    handle_temporal_compare,
)
from disclosure_agent.agent.period_retrieval import PeriodFactCollection
from disclosure_agent.agent.query_analyzer import (
    INTENT_TEMPORAL_COMPARE,
    QueryPlan,
    build_query_plan,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
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

# 합성 manifest에 있는 기업. 2024·2025 사업보고서를 모두 가지고 있다.
SYNTHETIC_COMPANY = "테스트반도체(주)"

SYNTHETIC_QUESTION = f"{SYNTHETIC_COMPANY} 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?"

# 대표 질문 원문. evaluation/questions.jsonl의 표기와 같아야 한다.
Q08 = "한미반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?"

REAL_COMPANY = "한미반도체"

# 합성 원천 값. 실제 공시 수치가 아니다.
#
# 2024: 333,333 / 1,000,000 -> 33.3333
# 2025: 456,789 / 1,234,567 -> 36.99993... -> 36.9999
# 변화량: 36.9999 - 33.3333 -> 3.6666
SOURCE_VALUES: dict[int, dict[MetricConcept, str]] = {
    2024: {
        MetricConcept.OPERATING_INCOME: "333333",
        MetricConcept.REVENUE: "1000000",
    },
    2025: {
        MetricConcept.OPERATING_INCOME: "456789",
        MetricConcept.REVENUE: "1234567",
    },
}

EXPECTED_MARGINS = {2024: "33.3333", 2025: "36.9999"}

EXPECTED_DIFF = "3.6666"


def _resolve_corpus_root() -> Path | None:
    raw = os.environ.get(CORPUS_ROOT_ENV)

    candidate = Path(raw) if raw else DEFAULT_CORPUS_ROOT

    if candidate.is_dir() and (candidate / "manifest.jsonl").is_file():
        return candidate

    return None


def _synthetic_fact(
    *,
    fiscal_year: int,
    metric: MetricConcept,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
) -> tuple[Fact, Evidence]:
    """확보 계층이 돌려주는 형태의 Fact/Evidence 한 쌍."""
    doc_id = f"periodic_SYNTHETIC_{fiscal_year}"

    evidence_id = f"evidence_{doc_id}_{metric.value}"

    evidence = Evidence(
        evidence_id=evidence_id,
        doc_id=doc_id,
        location_type=EvidenceLocationType.TABLE,
        section_path=["III. 재무에 관한 사항", "2. 연결재무제표"],
        row_label=metric.value,
        raw_text=SOURCE_VALUES[fiscal_year][metric],
    )

    fact = Fact(
        fact_id=f"fact_{doc_id}_{metric.value}",
        company=SYNTHETIC_COMPANY,
        metric=metric,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
        raw_value=SOURCE_VALUES[fiscal_year][metric],
        raw_unit="백만원",
        normalized_value=SOURCE_VALUES[fiscal_year][metric],
        normalized_unit="원",
        evidence_ids=[evidence_id],
    )

    return fact, evidence


def _synthetic_collection(
    *,
    scope_by_year: dict[int, FactAccountingScope] | None = None,
) -> PeriodFactCollection:
    """
    확보 계층이 Q08 요청에 돌려줄 결과.

    순서는 collect_period_facts와 같게 (회계연도, 지표)다. 요청 순서를 바꾸지 않는다는 것이
    그쪽의 계약이므로 여기서도 지킨다.
    """
    scopes = scope_by_year or {}

    facts: list[Fact] = []
    evidence: list[Evidence] = []

    for fiscal_year in (2024, 2025):
        for metric in (MetricConcept.OPERATING_INCOME, MetricConcept.REVENUE):
            fact, item = _synthetic_fact(
                fiscal_year=fiscal_year,
                metric=metric,
                accounting_scope=scopes.get(
                    fiscal_year, FactAccountingScope.CONSOLIDATED
                ),
            )

            facts.append(fact)
            evidence.append(item)

    return PeriodFactCollection(facts=tuple(facts), evidence=tuple(evidence))


@pytest.fixture
def collected(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """
    확보 결과를 합성으로 대신 채우고, 어떤 조건으로 요청했는지 기록한다.

    handler가 부르는 이름을 바꾼다. 확보 모듈 자체를 바꾸면 다른 Pattern까지 영향을 받는다.
    """
    captured: dict[str, object] = {}

    def _collect(**kwargs: object) -> PeriodFactCollection:
        captured.update(kwargs)

        return _synthetic_collection()

    monkeypatch.setattr(temporal_compare, "collect_period_facts", _collect)

    return captured


@pytest.fixture
def plan() -> QueryPlan:
    return build_query_plan(SYNTHETIC_QUESTION, known_companies=(SYNTHETIC_COMPANY,))


def _run(
    plan: QueryPlan,
    *,
    corpus_root: Path,
    manifest_path: Path | None = None,
):
    return handle_temporal_compare(
        plan,
        corpus_root=corpus_root,
        manifest_path=manifest_path,
    )


# --------------------------------------------------------------------------
# 1. Q08 모양 — Fact 4 / Calculation 3 / Comparison 0
# --------------------------------------------------------------------------


def test_question_is_planned_as_a_derived_metric_temporal_comparison(
    plan: QueryPlan,
) -> None:
    """전제 확인. 이 계획이 아니면 아래 검사가 다른 것을 재게 된다."""
    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.companies == (SYNTHETIC_COMPANY,)
    assert plan.years == (2024, 2025)
    assert plan.metric is None
    assert plan.derived_metric is DerivedMetric.OPERATING_MARGIN


def test_derived_metric_temporal_question_is_answerable(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert result.execution_issue is None


def test_result_carries_four_facts_three_calculations_and_no_comparison(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    개수는 evidence_and_answerability.md의 Calculation cardinality가 정한다.

    Comparison이 0인 것은 능력이 없어서가 아니라 질문이 우열 판정을 요구하지 않기 때문이다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    assert len(result.facts) == 4
    assert len(result.evidence) == 4
    assert len(result.calculations) == 3
    assert result.comparisons == []


def test_source_facts_cover_both_years_and_both_metrics(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    result = _run(plan, corpus_root=nfd_corpus_root)

    assert {(fact.fiscal_year, fact.metric) for fact in result.facts} == {
        (year, metric)
        for year in (2024, 2025)
        for metric in (MetricConcept.OPERATING_INCOME, MetricConcept.REVENUE)
    }


def test_calculations_are_two_ratios_and_one_change(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    순서는 이전 기간, 이후 기간, 변화량이다.

    질문에 적힌 순서가 아니라 시간 순서다. 적힌 순서를 쓰면 "2025년이랑 2024년"이라고 물은
    질문에서 부호가 뒤집힌 변화량이 나온다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    old, new, change = result.calculations

    assert (old.operation, new.operation) == (OPERATION_RATIO, OPERATION_RATIO)
    assert (old.unit, new.unit) == ("percent", "percent")
    assert (old.value, new.value) == (EXPECTED_MARGINS[2024], EXPECTED_MARGINS[2025])

    assert change.operation == OPERATION_DIFF_PP
    assert change.unit == UNIT_PERCENTAGE_POINT
    assert change.value == EXPECTED_DIFF


def test_calculation_identifiers_are_deterministic(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """같은 질문을 두 번 실행하면 같은 식별자가 나와야 한다."""
    first = _run(plan, corpus_root=nfd_corpus_root)

    second = _run(plan, corpus_root=nfd_corpus_root)

    identifiers = [item.calculation_id for item in first.calculations]

    assert identifiers == [item.calculation_id for item in second.calculations]

    assert identifiers == [
        "calculation_operating_margin_2024",
        "calculation_operating_margin_2025",
        "calculation_operating_margin_diff_pp_2024_2025",
    ]


def test_every_calculation_input_points_at_a_result_in_the_same_response(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    참조가 결과 밖을 가리키면 받는 쪽이 무엇에서 나온 값인지 되짚을 수 없다.

    evaluation.runner의 result_input_references 검사와 같은 성질을 여기서도 고정한다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    known = {
        ResultInputType.FACT: {fact.fact_id for fact in result.facts},
        ResultInputType.CALCULATION: {
            item.calculation_id for item in result.calculations
        },
    }

    dangling = [
        (reference.input_type, reference.input_id)
        for calculation in result.calculations
        for reference in calculation.inputs
        if reference.input_id not in known[reference.input_type]
    ]

    assert dangling == []


def test_each_margin_references_its_own_numerator_and_denominator(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """분자와 분모가 그 해의 것이어야 한다. 섞이면 어느 해의 비율도 아니다."""
    result = _run(plan, corpus_root=nfd_corpus_root)

    facts = {fact.fact_id: fact for fact in result.facts}

    for calculation, fiscal_year in zip(result.calculations[:2], (2024, 2025), strict=True):
        numerator, denominator = (
            facts[reference.input_id] for reference in calculation.inputs
        )

        assert numerator.metric is MetricConcept.OPERATING_INCOME
        assert denominator.metric is MetricConcept.REVENUE
        assert numerator.fiscal_year == fiscal_year
        assert denominator.fiscal_year == fiscal_year


def test_change_references_the_two_margins_in_chronological_order(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    result = _run(plan, corpus_root=nfd_corpus_root)

    old, new, change = result.calculations

    assert [(item.input_type, item.input_id) for item in change.inputs] == [
        (ResultInputType.CALCULATION, old.calculation_id),
        (ResultInputType.CALCULATION, new.calculation_id),
    ]


def test_every_calculation_evidence_reference_resolves(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """변화량은 두 기간의 근거를 모두 들고 있어야 한다."""
    result = _run(plan, corpus_root=nfd_corpus_root)

    available = {item.evidence_id for item in result.evidence}

    for calculation in result.calculations:
        assert calculation.evidence_ids
        assert not set(calculation.evidence_ids) - available

    assert set(result.calculations[2].evidence_ids) == available


def test_plan_warnings_survive_into_the_result(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    두 기간으로 해석한 근거는 성공한 답에도 남는다.

    질문을 어떻게 읽었는지가 사라지면 결과만 보고 해석을 되짚을 수 없다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    assert plan.warnings
    assert result.warnings == list(plan.warnings)


# --------------------------------------------------------------------------
# 2. 확보 요청 조건 — 대응표와 계획에서 나온다
# --------------------------------------------------------------------------


def test_source_metrics_are_expanded_from_the_registry(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    영업이익률을 그대로 확보하러 가지 않는다.

    파생 개념은 어느 문서에도 적혀 있지 않으므로, 확보 계층에 그대로 넘기면 지원하지 않는
    지표로 막힌다. 대응표가 분자와 분모로 펼친다.
    """
    _run(plan, corpus_root=nfd_corpus_root)

    assert collected["metrics"] == (
        MetricConcept.OPERATING_INCOME,
        MetricConcept.REVENUE,
    )


def test_every_requested_year_is_passed_to_retrieval(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    plan.years를 그대로 넘긴다.

    plan.year는 시계열 비교 계획에서 읽을 수 없는 통로이며, 읽으려 하면 예외가 난다. 한
    연도만 넘기면 두 기간을 물은 질문에 한 기간만 답하게 된다.
    """
    _run(plan, corpus_root=nfd_corpus_root)

    assert collected["fiscal_years"] == (2024, 2025)

    with pytest.raises(ValueError):
        plan.year  # noqa: B018 - 접근 자체가 검사 대상이다


def test_retrieval_conditions_come_from_the_plan(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    _run(plan, corpus_root=nfd_corpus_root)

    assert collected["company"] == SYNTHETIC_COMPANY
    assert collected["accounting_scope"] == plan.accounting_scope
    assert collected["document_class"] == plan.document_class
    assert collected["document_subtype"] == plan.document_subtype


# --------------------------------------------------------------------------
# 3. 다루지 못하는 시계열 계획은 값을 만들지 않는다
#
# Router가 열린 뒤 이 계획들이 handler로 들어온다. 무엇 하나 만들지 않고 막혀야 하며,
# facts나 evidence가 하나라도 남으면 축소해서 답했다는 뜻이다.
# --------------------------------------------------------------------------


def _temporal_plan(**overrides) -> QueryPlan:
    """검증에 필요한 축만 바꾼 시계열 비교 계획."""
    fields: dict[str, object] = {
        "question": "합성 질문",
        "companies": (SYNTHETIC_COMPANY,),
        "years": (2024, 2025),
        "intent": INTENT_TEMPORAL_COMPARE,
        "metric": None,
        "derived_metric": DerivedMetric.OPERATING_MARGIN,
    }

    fields.update(overrides)

    return QueryPlan(**fields)


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        # 원천 지표의 시계열 증감. delta·delta_pct가 없으므로 만들 수 없다.
        (
            {"metric": "revenue", "derived_metric": None},
            REASON_UNSUPPORTED_COMPARISON_METRIC,
        ),
        # 지표를 하나도 읽지 못한 질문.
        (
            {"metric": None, "derived_metric": None},
            REASON_UNSUPPORTED_COMPARISON_METRIC,
        ),
        # 절대연도 하나 + 해석하지 않은 상대 기간. F6-02가 막은 silent-wrong의 원본이다.
        ({"years": (2025,)}, REASON_UNSUPPORTED_COMPARISON_PERIOD),
        # 기간이 셋 이상인 추이 질문. 변화량 하나로 답할 수 없다.
        ({"years": (2023, 2024, 2025)}, REASON_UNSUPPORTED_COMPARISON_PERIOD),
        # 기업이 둘. 시계열 축과 기업 축이 섞였다.
        (
            {"companies": (SYNTHETIC_COMPANY, "테스트전자")},
            REASON_UNSUPPORTED_COMPARISON_SUBJECT,
        ),
    ],
)
def test_unsupported_temporal_plans_are_blocked_at_the_pattern_entry(
    overrides: dict[str, object],
    expected_reason: str,
    nfd_corpus_root: Path,
    synthetic_manifest_path: Path,
) -> None:
    """
    막는 자리가 Router에서 handler 입구로 옮겨왔을 뿐 결과의 성질은 같다.

    답변 가능성을 판정하지 않는다. 코퍼스를 열기 전이므로 답이 있는지 없는지 알지 못한다.
    """
    result = _run(
        _temporal_plan(**overrides),
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.answerability_reason is None
    assert result.facts == []
    assert result.evidence == []
    assert result.calculations == []

    issue = result.execution_issue

    assert issue is not None
    assert issue.stage == STAGE_PATTERN_ENTRY
    assert issue.reason == expected_reason


def test_source_metric_and_missing_metric_are_told_apart_in_the_detail() -> None:
    """
    같은 사유라도 후속 작업이 다르다.

    원천 지표의 시계열 증감은 delta를 구현하면 풀리고, 지표를 못 읽은 질문은 어휘를 넓혀야
    풀린다. 사유를 늘리는 대신 detail에서 나눈다.
    """
    source = build_temporal_compare_result(
        _temporal_plan(metric="revenue", derived_metric=None),
        corpus_root="unused",
    )

    missing = build_temporal_compare_result(
        _temporal_plan(metric=None, derived_metric=None),
        corpus_root="unused",
    )

    assert source.blocked is not None
    assert missing.blocked is not None
    assert source.blocked.reason == missing.blocked.reason
    assert source.blocked.detail != missing.blocked.detail
    assert "revenue" in source.blocked.detail


def test_unsupported_plan_never_opens_the_corpus(
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """
    막힌 계획에는 확보 자체가 일어나지 않는다.

    "확보했다가 마지막에 지웠다"와 다르다. 문서를 열지 않았으므로 Fact도 Evidence도
    만들어진 적이 없다.
    """

    def _forbidden(**kwargs: object) -> PeriodFactCollection:
        raise AssertionError(f"확보를 시도했습니다: {sorted(kwargs)}")

    monkeypatch.setattr(temporal_compare, "collect_period_facts", _forbidden)

    result = _run(_temporal_plan(years=(2025,)), corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.BLOCKED


def test_missing_filing_keeps_the_existing_retrieval_verdict(
    nfd_corpus_root: Path,
    synthetic_manifest_path: Path,
) -> None:
    """
    확보 계층의 정지 사유를 새로 해석하지 않는다.

    합성 manifest에는 2023년 사업보고서가 없다. 코퍼스를 끝까지 대조한 결과이므로 실행은
    끝났고 답이 없을 뿐이며, 그 판정은 이미 normalization의 대응표가 정하고 있다.
    """
    result = _run(
        _temporal_plan(years=(2023, 2025)),
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert result.facts == []
    assert result.calculations == []

    detail = result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_FILING_DISCOVERY


# --------------------------------------------------------------------------
# 4. guard② — 견줄 수 없는 두 기간
# --------------------------------------------------------------------------


def test_scope_mismatch_across_years_is_not_comparable(
    plan: QueryPlan,
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """
    2024년 연결과 2025년 별도는 각 연도의 guard①을 통과한다.

    연도 사이의 어긋남은 guard②만 잡을 수 있고, 놓치면 기준이 다른 두 비율의 차이가
    기간의 변화로 나간다.
    """
    monkeypatch.setattr(
        temporal_compare,
        "collect_period_facts",
        lambda **_: _synthetic_collection(
            scope_by_year={
                2024: FactAccountingScope.CONSOLIDATED,
                2025: FactAccountingScope.SEPARATE,
            }
        ),
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_COMPARABLE


def test_incomparable_result_keeps_the_facts_and_drops_every_calculation(
    plan: QueryPlan,
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
) -> None:
    """
    확보는 정상이었으므로 Fact와 Evidence는 남는다.

    파생값은 하나도 남기지 않는다. 기간별 비율 2건이 남아 있으면 받는 쪽이 그 둘을 빼서
    차이를 직접 만들 수 있고, 그러면 guard가 막은 일이 결과를 받는 쪽에서 그대로 일어난다.
    """
    monkeypatch.setattr(
        temporal_compare,
        "collect_period_facts",
        lambda **_: _synthetic_collection(
            scope_by_year={
                2024: FactAccountingScope.CONSOLIDATED,
                2025: FactAccountingScope.SEPARATE,
            }
        ),
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
    canonical 사유 하나로는 어느 guard가 막았는지 알 수 없다.

    계산 입력이 어긋난 것과 두 기간이 어긋난 것은 후속 조치가 다르므로 원문을 남긴다.
    """
    monkeypatch.setattr(
        temporal_compare,
        "collect_period_facts",
        lambda **_: _synthetic_collection(
            scope_by_year={
                2024: FactAccountingScope.CONSOLIDATED,
                2025: FactAccountingScope.SEPARATE,
            }
        ),
    )

    detail = _run(plan, corpus_root=nfd_corpus_root).answerability_detail

    assert detail is not None
    assert detail.reason == "incomparable_periods"
    assert detail.detail


# --------------------------------------------------------------------------
# 5. Router 경유
# --------------------------------------------------------------------------


def test_router_dispatches_the_intent_to_this_handler() -> None:
    assert router.resolve_handler(INTENT_TEMPORAL_COMPARE) is handle_temporal_compare


def test_question_string_reaches_the_same_result_through_the_router(
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
    synthetic_manifest_path: Path,
) -> None:
    """질문 문자열부터 실행 결과까지 한 번에 본다."""
    monkeypatch.setattr(
        temporal_compare,
        "collect_period_facts",
        lambda **_: _synthetic_collection(),
    )

    result = router.answer_question(
        SYNTHETIC_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert len(result.facts) == 4
    assert len(result.calculations) == 3


# --------------------------------------------------------------------------
# 6. 실측 코퍼스 — 대표 질문 Q08
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_q08_is_answerable_in_the_actual_corpus() -> None:
    """
    대표 질문 원문을 그대로 실행한다. 확보를 대신 채우지 않는다.

    수치는 실제 공시 값에서 나오므로 저장소에 적지 않는다(AGENTS.md). 여기서는 개수와
    단위, 참조 정합만 본다. 값 자체는 tests/unit/test_calculator.py가 합성 입력으로 고정한다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q08, corpus_root=corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None

    assert len(result.facts) == 4
    assert len(result.calculations) == 3
    assert result.comparisons == []

    old, new, change = result.calculations

    assert [item.unit for item in (old, new, change)] == [
        "percent",
        "percent",
        UNIT_PERCENTAGE_POINT,
    ]

    assert [item.operation for item in (old, new, change)] == [
        OPERATION_RATIO,
        OPERATION_RATIO,
        OPERATION_DIFF_PP,
    ]


@pytest.mark.corpus
def test_q08_references_resolve_inside_the_actual_result() -> None:
    """
    Fact -> Evidence, Calculation -> Evidence, Calculation -> Fact/Calculation.

    evaluation.runner가 gold와 대조하는 세 가지 정합을 같은 형태로 본다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q08, corpus_root=corpus_root)

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


@pytest.mark.corpus
def test_q08_source_facts_share_one_company_and_one_accounting_scope() -> None:
    """guard②가 실제로 통과할 조건이었는지 결과에서 확인한다."""
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q08, corpus_root=corpus_root)

    assert len({fact.company for fact in result.facts}) == 1
    assert {fact.accounting_scope for fact in result.facts} == {
        FactAccountingScope.CONSOLIDATED
    }
    assert {fact.fiscal_year for fact in result.facts} == {2024, 2025}

    assert REAL_COMPANY in next(iter({fact.company for fact in result.facts}))
