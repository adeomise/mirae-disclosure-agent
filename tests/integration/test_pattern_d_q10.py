"""Pattern D — Q10(원천 지표 기업 간 비교) 통합 테스트.

질문 문자열에서 공통 실행 Contract(AgentExecutionResult)까지의 흐름을 본다.

무엇을 고정하는가:
    1. Q10 하나가 Fact 2건 / Calculation 0건 / Comparison 1건이 된다.
    2. ComparisonResult의 left·right가 질문에 나온 기업 순서를 지키고, 그 참조가 같은 결과
       안의 Fact를 가리킨다.
    3. 견줄 수 없는 두 기업은 우열을 만들지 않는다(guard②).
    4. 그 값으로 산술이 성립하지 않는 경우는 답변 불가가 아니라 실행 중단이다.

Calculation을 만들지 않는 것이 이 Pattern의 성질이다:
    Q10은 원문 Fact 두 개를 그대로 견주므로 수치형 파생 결과가 없다
    (evidence_and_answerability.md Calculation cardinality). 차이값 하나만 실려도 개수 계약이
    깨지므로, 아래 여러 곳에서 calculations == []를 함께 본다.

확보를 대신 채우는 이유:
    확보 흐름 자체는 tests/unit/test_period_retrieval.py가 실측 코퍼스까지 포함해 이미
    고정하고 있다. 여기서 보는 것은 확보 이후의 판정과 조립이며, 합성 값으로 두어야 우열이
    뒤집히는 경우와 견줄 수 없는 경우를 모두 만들어 볼 수 있다.

    확보 요청 조건이 계획에서 나왔는지는 따로 검사한다(2절). 실제 코퍼스가 있으면 대신 채우지
    않고 대표 질문을 끝까지 돌린다(7절).

    실제 공시 수치는 쓰지 않는다(AGENTS.md). 아래 숫자는 대소 관계만 의미를 가지는 합성 값이다.

두 개의 '같은 기업' 판정:
    계획에 같은 기업이 두 번 담긴 경우는 코퍼스를 열기 전에 pattern_entry에서 막고, 서로
    다른 기업을 요청했는데 확보된 Fact의 기업이 같은 경우는 guard②가 comparability에서
    막는다. 앞쪽은 문서를 두 번 열지 않기 위한 것이고 뒤쪽은 확보 결과에 대한 판정이므로,
    둘은 서로를 대신하지 않는다. 5절과 4절에서 각각 본다.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.answer_builder import (
    REASON_INCOMPARABLE_ENTITIES,
    REASON_NO_MATCHING_FILING,
    REASON_UNSUPPORTED_COMPARISON_METRIC,
    REASON_UNSUPPORTED_COMPARISON_PERIOD,
    REASON_UNSUPPORTED_COMPARISON_SUBJECT,
    REASON_UNUSABLE_CALCULATION_INPUT,
    STAGE_COMPARABILITY,
    STAGE_FILING_DISCOVERY,
    STAGE_PATTERN_ENTRY,
    BlockedInfo,
)
from disclosure_agent.agent.handlers import cross_entity_compare
from disclosure_agent.agent.handlers.cross_entity_compare import (
    CrossEntityShape,
    build_cross_entity_compare_result,
    handle_cross_entity_compare,
    resolve_shape,
)
from disclosure_agent.agent.period_retrieval import PeriodFactCollection
from disclosure_agent.agent.query_analyzer import (
    INTENT_CROSS_ENTITY_COMPARE,
    PREVIOUS_YEAR_OFFSET,
    QueryPlan,
    RelativePeriod,
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

# 합성 manifest에 있는 두 기업. 둘 다 2025 사업보고서를 가지고 있다.
COMPANY_LEFT = "테스트반도체(주)"
COMPANY_RIGHT = "테스트전자"

SYNTHETIC_QUESTION = f"2025년 {COMPANY_LEFT}랑 {COMPANY_RIGHT} 매출 비교해줘"

# 대표 질문 원문. evaluation/questions.jsonl의 표기와 같아야 한다.
Q10 = "2025년 HD현대중공업이랑 삼성중공업 매출 비교해줘."

Q10_COMPANIES = ("HD현대중공업", "삼성중공업")

FISCAL_YEAR = 2025

# 합성 원천 값. 실제 공시 수치가 아니다.
DEFAULT_VALUES: dict[str, str] = {COMPANY_LEFT: "200", COMPANY_RIGHT: "100"}


# --------------------------------------------------------------------------
# 합성 확보 결과
# --------------------------------------------------------------------------


def _synthetic_pair(
    company: str,
    *,
    value: str | None,
    unit: str | None = "원",
    scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
    metric: MetricConcept = MetricConcept.REVENUE,
    fiscal_year: int = FISCAL_YEAR,
    fact_company: str | None = None,
) -> tuple[Fact, Evidence]:
    """확보 계층이 돌려주는 형태의 Fact/Evidence 한 쌍."""
    doc_id = f"periodic_SYNTHETIC_{company}"

    evidence_id = f"evidence_{doc_id}_{metric.value}"

    evidence = Evidence(
        evidence_id=evidence_id,
        doc_id=doc_id,
        location_type=EvidenceLocationType.TABLE,
        section_path=["III. 재무에 관한 사항", "2. 연결재무제표"],
        row_label=metric.value,
        raw_text=value,
    )

    fact = Fact(
        fact_id=f"fact_{doc_id}_{metric.value}",
        # 확보된 Fact의 기업명은 문서 metadata의 corp_name이며, 계획에 담긴 표기와 다를 수
        # 있다. 실측 코퍼스에서 'HD현대중공업'을 물으면 '에이치디현대중공업(주)'가 돌아온다.
        company=fact_company if fact_company is not None else company,
        metric=metric,
        fiscal_year=fiscal_year,
        accounting_scope=scope,
        raw_value=value,
        raw_unit="백만원",
        normalized_value=value,
        normalized_unit=unit,
        evidence_ids=[evidence_id],
    )

    return fact, evidence


def _collection_for(company: str, **overrides) -> PeriodFactCollection:
    fact, evidence = _synthetic_pair(
        company, value=DEFAULT_VALUES.get(company, "100"), **overrides
    )

    return PeriodFactCollection(facts=(fact,), evidence=(evidence,))


@pytest.fixture
def collected(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """
    기업별 확보 결과를 합성으로 대신 채우고, 마지막 요청 조건을 기록한다.

    handler가 부르는 이름을 바꾼다. 확보 모듈 자체를 바꾸면 다른 Pattern까지 영향을 받는다.
    """
    captured: dict[str, object] = {}

    def _collect(**kwargs: object) -> PeriodFactCollection:
        captured.setdefault("calls", []).append(dict(kwargs))  # type: ignore[union-attr]

        captured.update(kwargs)

        return _collection_for(str(kwargs["company"]))

    captured["calls"] = []

    monkeypatch.setattr(cross_entity_compare, "collect_period_facts", _collect)

    return captured


@pytest.fixture
def patch_collection(monkeypatch: pytest.MonkeyPatch):
    """기업별 확보 결과를 직접 정하고 싶을 때 쓴다."""

    def _patch(by_company: dict[str, PeriodFactCollection]) -> None:
        monkeypatch.setattr(
            cross_entity_compare,
            "collect_period_facts",
            lambda **kwargs: by_company[str(kwargs["company"])],
        )

    return _patch


@pytest.fixture
def plan() -> QueryPlan:
    return build_query_plan(
        SYNTHETIC_QUESTION, known_companies=(COMPANY_LEFT, COMPANY_RIGHT)
    )


def _run(plan: QueryPlan, *, corpus_root: Path, manifest_path: Path | None = None):
    return handle_cross_entity_compare(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )


def _resolve_corpus_root() -> Path | None:
    raw = os.environ.get(CORPUS_ROOT_ENV)

    candidate = Path(raw) if raw else DEFAULT_CORPUS_ROOT

    if candidate.is_dir() and (candidate / "manifest.jsonl").is_file():
        return candidate

    return None


# --------------------------------------------------------------------------
# 1. Q10 모양 — Fact 2 / Calculation 0 / Comparison 1
# --------------------------------------------------------------------------


def test_question_is_planned_as_a_source_metric_cross_entity_comparison(
    plan: QueryPlan,
) -> None:
    """전제 확인. 이 계획이 아니면 아래 검사가 다른 것을 재게 된다."""
    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE
    assert plan.companies == (COMPANY_LEFT, COMPANY_RIGHT)
    assert plan.years == (FISCAL_YEAR,)
    assert plan.metric == MetricConcept.REVENUE.value
    assert plan.derived_metric is None
    assert resolve_shape(plan) is CrossEntityShape.SOURCE_METRIC


def test_source_metric_cross_entity_question_is_answerable(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert result.execution_issue is None


def test_result_carries_two_facts_no_calculation_and_one_comparison(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    개수는 evidence_and_answerability.md의 Calculation cardinality가 정한다.

    Calculation이 0인 것은 계산 능력이 없어서가 아니라 질문이 수치형 파생 결과를 요구하지
    않기 때문이다. 두 값의 차이를 하나라도 만들면 이 검사가 걸린다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    assert len(result.facts) == 2
    assert len(result.evidence) == 2
    assert result.calculations == []
    assert len(result.comparisons) == 1


def test_facts_cover_both_companies_for_the_requested_metric_and_year(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    result = _run(plan, corpus_root=nfd_corpus_root)

    assert [fact.company for fact in result.facts] == [COMPANY_LEFT, COMPANY_RIGHT]

    assert {fact.metric for fact in result.facts} == {MetricConcept.REVENUE}

    assert {fact.fiscal_year for fact in result.facts} == {FISCAL_YEAR}


def test_every_fact_evidence_reference_resolves(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    result = _run(plan, corpus_root=nfd_corpus_root)

    available = {item.evidence_id for item in result.evidence}

    for fact in result.facts:
        assert fact.evidence_ids
        assert not set(fact.evidence_ids) - available


def test_comparison_identifier_is_deterministic(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    같은 질문을 두 번 실행하면 같은 식별자가 나와야 한다.

    난수를 쓰면 평가와 회귀가 결과를 대조할 때마다 다른 값을 본다. 식별자에 담기는 기업
    표기는 계획에 담긴 것이며, 확보 결과의 표기를 쓰면 같은 질문의 식별자가 확보 계층에
    매인다.
    """
    first = _run(plan, corpus_root=nfd_corpus_root)

    second = _run(plan, corpus_root=nfd_corpus_root)

    assert (
        first.comparisons[0].comparison_id == second.comparisons[0].comparison_id
    )

    assert first.comparisons[0].comparison_id == (
        f"comparison_{MetricConcept.REVENUE.value}_{FISCAL_YEAR}"
        f"_{COMPANY_LEFT}_vs_{COMPANY_RIGHT}"
    )


# --------------------------------------------------------------------------
# 2. 확보 요청 조건 — 계획에서 나온다
# --------------------------------------------------------------------------


def test_each_company_is_collected_separately_in_question_order(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    확보는 기업마다 한 번씩이다.

    확보 계층은 기업 하나를 받는다. 기업 축을 그쪽에 넣으면 Pattern마다 다른 축이 확보
    계층으로 새어 들어간다.
    """
    _run(plan, corpus_root=nfd_corpus_root)

    calls = collected["calls"]

    assert [call["company"] for call in calls] == [COMPANY_LEFT, COMPANY_RIGHT]


def test_retrieval_asks_for_the_requested_metric_and_single_year(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    파생 개념이 아니므로 대응표를 거치지 않고 계획의 원천 지표를 그대로 요청한다.

    기간은 하나다. 여기서 두 연도를 넘기면 Q10이 시계열 비교로 바뀐다.
    """
    _run(plan, corpus_root=nfd_corpus_root)

    for call in collected["calls"]:
        assert call["metrics"] == (MetricConcept.REVENUE,)
        assert call["fiscal_years"] == (FISCAL_YEAR,)
        assert call["accounting_scope"] == plan.accounting_scope
        assert call["document_class"] == plan.document_class
        assert call["document_subtype"] == plan.document_subtype


# --------------------------------------------------------------------------
# 3. ComparisonResult — 순서와 관계, 그리고 참조
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left_value", "right_value", "expected"),
    [
        ("200", "100", ComparisonRelation.LEFT_GREATER),
        ("100", "200", ComparisonRelation.RIGHT_GREATER),
        ("150", "150", ComparisonRelation.EQUAL),
        # 표기가 달라도 값이 같으면 같다. 문자열 비교로 판정하면 여기서 갈린다.
        ("150", "150.00", ComparisonRelation.EQUAL),
    ],
)
def test_relation_follows_the_normalized_values(
    left_value: str,
    right_value: str,
    expected: ComparisonRelation,
    plan: QueryPlan,
    patch_collection,
    nfd_corpus_root: Path,
) -> None:
    patch_collection(
        {
            COMPANY_LEFT: PeriodFactCollection(
                facts=(_synthetic_pair(COMPANY_LEFT, value=left_value)[0],),
                evidence=(_synthetic_pair(COMPANY_LEFT, value=left_value)[1],),
            ),
            COMPANY_RIGHT: PeriodFactCollection(
                facts=(_synthetic_pair(COMPANY_RIGHT, value=right_value)[0],),
                evidence=(_synthetic_pair(COMPANY_RIGHT, value=right_value)[1],),
            ),
        }
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.comparisons[0].relation is expected


def test_left_and_right_follow_the_order_in_the_question(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    정렬하지 않는다.

    정렬하면 "A랑 B 비교해줘"라고 물은 질문에 B가 left인 판정이 돌아가고, 관계 이름이
    뒤집혀도 그럴듯하다.
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    facts = {fact.fact_id: fact for fact in result.facts}

    comparison = result.comparisons[0]

    assert facts[comparison.left.input_id].company == COMPANY_LEFT
    assert facts[comparison.right.input_id].company == COMPANY_RIGHT


def test_reversing_the_question_order_reverses_the_relation(
    patch_collection,
    nfd_corpus_root: Path,
) -> None:
    """
    같은 두 값이라도 질문에 나온 순서가 바뀌면 left/right가 바뀐다.

    관계 이름이 그 순서를 따라간다는 것이 이 판정의 규약이며, 값 자체의 우열은 변하지 않는다.
    """
    reversed_plan = build_query_plan(
        f"2025년 {COMPANY_RIGHT}랑 {COMPANY_LEFT} 매출 비교해줘",
        known_companies=(COMPANY_LEFT, COMPANY_RIGHT),
    )

    assert reversed_plan.companies == (COMPANY_RIGHT, COMPANY_LEFT)

    patch_collection(
        {company: _collection_for(company) for company in (COMPANY_LEFT, COMPANY_RIGHT)}
    )

    result = _run(reversed_plan, corpus_root=nfd_corpus_root)

    # 값은 그대로 테스트반도체(200) > 테스트전자(100)인데 순서가 뒤집혔으므로 right가 크다.
    assert result.comparisons[0].relation is ComparisonRelation.RIGHT_GREATER

    facts = {fact.fact_id: fact for fact in result.facts}

    assert facts[result.comparisons[0].left.input_id].company == COMPANY_RIGHT


def test_comparison_inputs_point_at_facts_in_the_same_response(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """
    참조가 결과 밖을 가리키면 받는 쪽이 무엇을 견줬는지 되짚을 수 없다.

    ComparisonResult에는 evidence_ids가 없으므로, 이 참조가 정합의 유일한 통로다
    (tests/unit/test_pattern_d_corpus_regression_expectations.py 3절).
    """
    result = _run(plan, corpus_root=nfd_corpus_root)

    known = {fact.fact_id for fact in result.facts}

    comparison = result.comparisons[0]

    for reference in (comparison.left, comparison.right):
        assert reference.input_type is ResultInputType.FACT
        assert reference.input_id in known

    assert comparison.left.input_id != comparison.right.input_id


# --------------------------------------------------------------------------
# 4. guard② — 견줄 수 없는 두 기업
#
# 전부 COMPLETED + UNANSWERABLE + NOT_COMPARABLE이다. 값을 다 확보한 뒤에 내린 판정이므로
# 실행은 끝났고, Fact와 Evidence는 남고 Comparison은 남지 않는다.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left_overrides", "right_overrides", "expected_in_detail"),
    [
        # 단위 정규화 실패. Q10에서 이 검사가 걸리는 자리는 guard② 하나뿐이다.
        ({"unit": "원"}, {"unit": "천원"}, "단위"),
        # 한쪽만 단위를 확정하지 못한 경우.
        ({"unit": "원"}, {"unit": None}, "정규화 단위가 없는"),
        # 연결과 별도를 섞었다.
        (
            {"scope": FactAccountingScope.CONSOLIDATED},
            {"scope": FactAccountingScope.SEPARATE},
            "회계기준",
        ),
        # 양쪽 모두 기준을 확정하지 못했다. "서로 같은가"만 보면 통과한다.
        (
            {"scope": FactAccountingScope.UNKNOWN},
            {"scope": FactAccountingScope.UNKNOWN},
            "확정하지 못한",
        ),
        # 확보된 두 Fact의 기업이 같다. 계획은 서로 다른 두 기업을 요청했다.
        ({}, {"fact_company": COMPANY_LEFT}, "기업이 같습니다"),
        # 한쪽만 다른 회계연도의 값이 돌아왔다.
        ({}, {"fiscal_year": 2024}, "회계연도"),
    ],
)
def test_incomparable_entities_are_not_judged(
    left_overrides: dict[str, object],
    right_overrides: dict[str, object],
    expected_in_detail: str,
    plan: QueryPlan,
    patch_collection,
    nfd_corpus_root: Path,
) -> None:
    left_fact, left_evidence = _synthetic_pair(
        COMPANY_LEFT, value="200", **left_overrides
    )

    right_fact, right_evidence = _synthetic_pair(
        COMPANY_RIGHT, value="100", **right_overrides
    )

    patch_collection(
        {
            COMPANY_LEFT: PeriodFactCollection(
                facts=(left_fact,), evidence=(left_evidence,)
            ),
            COMPANY_RIGHT: PeriodFactCollection(
                facts=(right_fact,), evidence=(right_evidence,)
            ),
        }
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_COMPARABLE

    detail = result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_COMPARABILITY
    assert detail.reason == REASON_INCOMPARABLE_ENTITIES
    assert expected_in_detail in detail.detail


def test_incomparable_result_keeps_the_facts_and_drops_the_comparison(
    plan: QueryPlan,
    patch_collection,
    nfd_corpus_root: Path,
) -> None:
    """
    확보는 정상이었으므로 Fact와 Evidence는 남는다.

    우열 판정은 남기지 않는다. 남아 있으면 guard가 막은 비교가 결과를 받는 쪽에서 그대로
    성립한다. Calculation도 0인 것은 이 Pattern이 애초에 만들지 않기 때문이다.
    """
    patch_collection(
        {
            COMPANY_LEFT: _collection_for(COMPANY_LEFT, unit="원"),
            COMPANY_RIGHT: _collection_for(COMPANY_RIGHT, unit="천원"),
        }
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert len(result.facts) == 2
    assert len(result.evidence) == 2
    assert result.calculations == []
    assert result.comparisons == []


def test_unit_mismatch_is_caught_even_though_this_pattern_has_no_calculation_guard(
    plan: QueryPlan,
    patch_collection,
    nfd_corpus_root: Path,
) -> None:
    """
    guard①이 없는 경로에서 단위 검사가 빠지지 않는지 본다.

    실측 코퍼스에서 두 기업의 원문 단위 표기가 실제로 다르다(천원 / 원). 정규화 뒤에도 단위가
    갈리면 자릿수가 세 자리 어긋난 우열이 그대로 나가며, Q10이 검증하려는 실패가 그것이다
    (representative_questions.md 유형 4).
    """
    patch_collection(
        {
            COMPANY_LEFT: _collection_for(COMPANY_LEFT, unit="천원"),
            COMPANY_RIGHT: _collection_for(COMPANY_RIGHT, unit="원"),
        }
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.answerability_reason is AnswerabilityReason.NOT_COMPARABLE
    assert result.comparisons == []


# --------------------------------------------------------------------------
# 5. 실행 중단 — 그 값으로는 판정 자체가 성립하지 않는다
#
# 답변 불가와 나눈다. 근거가 없는 것도 코퍼스에 답이 없는 것도 아니므로 대응표에 없고,
# 그래서 BLOCKED로 남는다(agent.normalization의 unusable_calculation_input 주석).
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "해당사항없음", "NaN", "Infinity"])
def test_unusable_numeric_input_blocks_the_execution(
    value: str | None,
    plan: QueryPlan,
    patch_collection,
    nfd_corpus_root: Path,
) -> None:
    patch_collection(
        {
            COMPANY_LEFT: _collection_for(COMPANY_LEFT),
            COMPANY_RIGHT: PeriodFactCollection(
                facts=(_synthetic_pair(COMPANY_RIGHT, value=value)[0],),
                evidence=(_synthetic_pair(COMPANY_RIGHT, value=value)[1],),
            ),
        }
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.answerability_reason is None
    assert result.comparisons == []

    issue = result.execution_issue

    assert issue is not None
    assert issue.stage == STAGE_COMPARABILITY
    assert issue.reason == REASON_UNUSABLE_CALCULATION_INPUT


def test_blocked_numeric_input_still_shows_which_values_were_collected(
    plan: QueryPlan,
    patch_collection,
    nfd_corpus_root: Path,
) -> None:
    """
    확보 자체는 정상이었으므로 Fact와 Evidence는 남는다.

    Pattern C가 같은 자리에서 하는 것과 같다. 어느 값이 문제였는지 보려면 그 값이 있어야 한다.
    """
    patch_collection(
        {
            COMPANY_LEFT: _collection_for(COMPANY_LEFT),
            COMPANY_RIGHT: PeriodFactCollection(
                facts=(_synthetic_pair(COMPANY_RIGHT, value=None)[0],),
                evidence=(_synthetic_pair(COMPANY_RIGHT, value=None)[1],),
            ),
        }
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert len(result.facts) == 2
    assert len(result.evidence) == 2
    assert result.calculations == []


# --------------------------------------------------------------------------
# 6. 이번 구현이 다루지 못하는 계획은 값을 만들지 않는다
#
# 전부 코퍼스를 열기 전에 막힌다. facts나 evidence가 하나라도 남으면 축소해서 답했다는 뜻이다.
# --------------------------------------------------------------------------


def _cross_entity_plan(**overrides) -> QueryPlan:
    """검증에 필요한 축만 바꾼 기업 간 비교 계획."""
    fields: dict[str, object] = {
        "question": SYNTHETIC_QUESTION,
        "companies": (COMPANY_LEFT, COMPANY_RIGHT),
        "years": (FISCAL_YEAR,),
        "intent": INTENT_CROSS_ENTITY_COMPARE,
        "metric": MetricConcept.REVENUE.value,
        "derived_metric": None,
    }

    fields.update(overrides)

    return QueryPlan(**fields)


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        # 같은 기업을 두 번 담은 계획. 견줄 것이 없다.
        (
            {"companies": (COMPANY_LEFT, COMPANY_LEFT)},
            REASON_UNSUPPORTED_COMPARISON_SUBJECT,
        ),
        # 기업이 셋.
        (
            {"companies": (COMPANY_LEFT, COMPANY_RIGHT, "테스트화학")},
            REASON_UNSUPPORTED_COMPARISON_SUBJECT,
        ),
        # 기업 축과 기간 축이 함께 여럿이다.
        ({"years": (2024, 2025)}, REASON_UNSUPPORTED_COMPARISON_PERIOD),
        # 절대연도 하나 + 해석하지 않은 상대 기간. 개수만 보면 통과한다.
        #
        # 상대 기간은 질문 문자열이 아니라 계획에 담아 준다. 막는 쪽이 읽는 정본이
        # QueryPlan.has_unresolved_relative_period이기 때문이며, 그 값을 채우는 자리는
        # query_analyzer._build_cross_entity_compare_plan이다. 문자열로만 두면 실제
        # 계획과 다른 모양을 검증하게 된다.
        (
            {
                "question": f"{COMPANY_LEFT}랑 {COMPANY_RIGHT} 2025년 매출이 전년보다 어때",
                "relative_period": RelativePeriod(
                    expression="전년",
                    offset=PREVIOUS_YEAR_OFFSET,
                ),
            },
            REASON_UNSUPPORTED_COMPARISON_PERIOD,
        ),
        # 이번 범위 밖의 원천 지표.
        (
            {"metric": MetricConcept.CONTRACT_AMOUNT.value},
            REASON_UNSUPPORTED_COMPARISON_METRIC,
        ),
        # 지표를 하나도 읽지 못한 계획(Q12가 이 상태다).
        ({"metric": None}, REASON_UNSUPPORTED_COMPARISON_METRIC),
    ],
)
def test_unsupported_plans_are_blocked_at_the_pattern_entry(
    overrides: dict[str, object],
    expected_reason: str,
    monkeypatch: pytest.MonkeyPatch,
    nfd_corpus_root: Path,
    synthetic_manifest_path: Path,
) -> None:
    """
    막힌 계획에는 확보 자체가 일어나지 않는다.

    "확보했다가 마지막에 지웠다"와 다르다. 문서를 열지 않았으므로 Fact도 Evidence도 만들어진
    적이 없다. 답변 가능성도 판정하지 않는다. 코퍼스를 보지 않았기 때문이다.
    """

    def _forbidden(**kwargs: object) -> PeriodFactCollection:
        raise AssertionError(f"확보를 시도했습니다: {sorted(kwargs)}")

    monkeypatch.setattr(cross_entity_compare, "collect_period_facts", _forbidden)

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


def test_missing_filing_keeps_the_existing_retrieval_verdict(
    nfd_corpus_root: Path,
    synthetic_manifest_path: Path,
) -> None:
    """
    확보 계층의 정지 사유를 새로 해석하지 않는다.

    합성 manifest에는 2023년 사업보고서가 없다. 코퍼스를 끝까지 대조한 결과이므로 실행은
    끝났고 답이 없을 뿐이며, 그 판정은 이미 normalization의 대응표가 정하고 있다. 어느
    기업에서 막혔는지는 detail에만 덧붙는다.
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
    assert result.comparisons == []

    detail = result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_FILING_DISCOVERY
    assert COMPANY_LEFT in detail.detail


def test_a_missing_second_company_drops_the_first_company_too(
    plan: QueryPlan,
    patch_collection,
    nfd_corpus_root: Path,
) -> None:
    """
    한쪽만 확보된 결과를 남기지 않는다.

    A사는 찾았는데 B사 사업보고서가 없을 때 A사 Fact가 결과에 남아 있으면, 받는 쪽이 그것만
    으로 답을 조립할 수 있다. 그러면 두 기업을 물은 질문에 한 기업만 답하는 결과가 되고,
    그것이 F6-04에서 막은 동작이다. 확보 계층이 (연도 x 지표)에 대해 이미 지키는
    전부-또는-전무를 기업 축으로 넓힌다.
    """
    patch_collection(
        {
            COMPANY_LEFT: _collection_for(COMPANY_LEFT),
            COMPANY_RIGHT: PeriodFactCollection(
                blocked=BlockedInfo(
                    stage=STAGE_FILING_DISCOVERY,
                    reason=REASON_NO_MATCHING_FILING,
                    detail=f"{FISCAL_YEAR} 회계연도 / revenue: 조건에 맞는 공시가 없습니다.",
                )
            ),
        }
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND

    # 첫 기업은 확보에 성공했지만 결과에 남지 않는다.
    assert result.facts == []
    assert result.evidence == []
    assert result.comparisons == []

    detail = result.answerability_detail

    assert detail is not None
    assert COMPANY_RIGHT in detail.detail


def test_missing_metric_in_the_collection_blocks_instead_of_guessing(
    plan: QueryPlan,
    patch_collection,
    nfd_corpus_root: Path,
) -> None:
    """
    확보 계약이 어긋났을 때 그 사실이 값이 아니라 정지 지점으로 드러나야 한다.

    정상 경로에서는 일어나지 않는다. collect_period_facts가 요청한 조합을 전부 확보하거나
    하나도 담지 않기 때문이다.
    """
    patch_collection(
        {
            COMPANY_LEFT: _collection_for(COMPANY_LEFT),
            COMPANY_RIGHT: PeriodFactCollection(
                facts=(
                    _synthetic_pair(
                        COMPANY_RIGHT,
                        value="100",
                        metric=MetricConcept.OPERATING_INCOME,
                    )[0],
                ),
                evidence=(),
            ),
        }
    )

    result = _run(plan, corpus_root=nfd_corpus_root)

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.comparisons == []

    issue = result.execution_issue

    assert issue is not None
    assert issue.reason == REASON_UNUSABLE_CALCULATION_INPUT


def test_router_dispatches_the_intent_to_this_handler() -> None:
    assert (
        router.resolve_handler(INTENT_CROSS_ENTITY_COMPARE)
        is cross_entity_compare.handle_cross_entity_compare
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
        lambda **kwargs: _collection_for(str(kwargs["company"])),
    )

    result = router.answer_question(
        SYNTHETIC_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert len(result.facts) == 2
    assert result.calculations == []
    assert len(result.comparisons) == 1


def test_internal_result_is_serializable_for_diagnostics(
    plan: QueryPlan,
    collected: dict[str, object],
    nfd_corpus_root: Path,
) -> None:
    """Pattern 내부 표현이 진단용으로 그대로 읽혀야 한다."""
    structured = build_cross_entity_compare_result(plan, corpus_root=nfd_corpus_root)

    payload = structured.to_dict()

    assert payload["intent"] == INTENT_CROSS_ENTITY_COMPARE
    assert payload["blocked"] is None
    assert len(payload["comparisons"]) == 1
    assert payload["calculations"] == []


# --------------------------------------------------------------------------
# 7. 실측 코퍼스 — 대표 질문 Q10
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_q10_is_answerable_in_the_actual_corpus() -> None:
    """
    대표 질문 원문을 그대로 실행한다. 확보를 대신 채우지 않는다.

    수치는 실제 공시 값에서 나오므로 저장소에 적지 않는다(AGENTS.md). 여기서는 개수와 관계,
    참조 정합만 본다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q10, corpus_root=corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None

    assert len(result.facts) == 2
    assert len(result.evidence) == 2
    assert result.calculations == []
    assert len(result.comparisons) == 1


@pytest.mark.corpus
def test_q10_compares_two_distinct_companies_on_one_normalized_unit() -> None:
    """
    guard②가 실제로 통과할 조건이었는지 결과에서 확인한다.

    이 코퍼스에서 두 기업의 원문 단위 표기가 서로 다르다. 그래서 정규화 단위가 하나로 모였다는
    사실이 이 질문에서 의미를 갖는다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q10, corpus_root=corpus_root)

    assert len({fact.company for fact in result.facts}) == 2

    assert {fact.metric for fact in result.facts} == {MetricConcept.REVENUE}
    assert {fact.fiscal_year for fact in result.facts} == {FISCAL_YEAR}
    assert {fact.accounting_scope for fact in result.facts} == {
        FactAccountingScope.CONSOLIDATED
    }

    assert len({fact.normalized_unit for fact in result.facts}) == 1

    for fact in result.facts:
        assert fact.normalized_unit is not None
        assert fact.normalized_value is not None


@pytest.mark.corpus
def test_q10_relation_matches_the_normalized_values_in_the_actual_result() -> None:
    """
    관계가 결과에 실린 값과 일치해야 한다.

    수치를 저장소에 적지 않고도 판정의 정당성을 확인하는 방법이다. 우열을 상수로 적으면 실제
    공시 값을 적는 것과 같아진다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q10, corpus_root=corpus_root)

    facts = {fact.fact_id: fact for fact in result.facts}

    comparison = result.comparisons[0]

    left = Decimal(str(facts[comparison.left.input_id].normalized_value))

    right = Decimal(str(facts[comparison.right.input_id].normalized_value))

    expected = (
        ComparisonRelation.LEFT_GREATER
        if left > right
        else ComparisonRelation.RIGHT_GREATER
        if left < right
        else ComparisonRelation.EQUAL
    )

    assert comparison.relation is expected


@pytest.mark.corpus
def test_q10_left_and_right_follow_the_company_order_in_the_question() -> None:
    """
    질문에 나온 순서가 left/right 순서다.

    확보된 Fact의 기업 표기는 문서 metadata에서 오므로 질문의 표기와 다를 수 있다
    ('HD현대중공업' -> '에이치디현대중공업(주)'). 그래서 문자열이 같은지가 아니라 어느 공시에서
    나온 값인지로 순서를 확인한다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q10, corpus_root=corpus_root)

    facts = {fact.fact_id: fact for fact in result.facts}

    comparison = result.comparisons[0]

    assert facts[comparison.left.input_id].fact_id == result.facts[0].fact_id
    assert facts[comparison.right.input_id].fact_id == result.facts[1].fact_id

    assert comparison.comparison_id == (
        f"comparison_{MetricConcept.REVENUE.value}_{FISCAL_YEAR}"
        f"_{Q10_COMPANIES[0]}_vs_{Q10_COMPANIES[1]}"
    )


@pytest.mark.corpus
def test_q10_references_resolve_inside_the_actual_result() -> None:
    """Fact -> Evidence, Comparison -> Fact. evaluation.runner가 보는 정합과 같은 성질이다."""
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = router.answer_question(Q10, corpus_root=corpus_root)

    evidence_ids = {item.evidence_id for item in result.evidence}

    fact_ids = {fact.fact_id for fact in result.facts}

    for fact in result.facts:
        assert fact.evidence_ids
        assert not set(fact.evidence_ids) - evidence_ids

    for comparison in result.comparisons:
        for reference in (comparison.left, comparison.right):
            assert reference.input_type is ResultInputType.FACT
            assert reference.input_id in fact_ids
