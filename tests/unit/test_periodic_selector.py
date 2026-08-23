"""Contract-only tests for targeted periodic Fact selection."""

import pytest

from disclosure_agent.common.enums import (
    DocumentClass,
    EvidenceLocationType,
    FactAccountingScope,
    Intent,
    MetricConcept,
    QueryAccountingScope,
)
from disclosure_agent.common.schemas import Entity, Evidence, Fact, Period, QueryPlan
from disclosure_agent.periodic.selector import select_periodic_fact


def query_plan() -> QueryPlan:
    return QueryPlan(
        query_id="q01-selector-test",
        original_query="dummy question",
        intent=Intent.SINGLE_FACT,
        entities=[Entity(surface="dummy", resolved_name="dummy", role="company")],
        periods=[Period(kind="fiscal_year", year=2025, period_type="FY")],
        document_class=DocumentClass.PERIODIC,
        accounting_scope=QueryAccountingScope.CONSOLIDATED,
        metrics=[MetricConcept.REVENUE],
    )


def fact(
    *,
    fact_id: str = "fact_dummy_revenue",
    fiscal_year: int = 2025,
    fiscal_period: str = "annual",
    scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
    evidence_ids: list[str] | None = None,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        company="dummy-company",
        metric=MetricConcept.REVENUE,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        accounting_scope=scope,
        raw_value="100",
        raw_unit="dummy-unit",
        evidence_ids=evidence_ids or ["evidence_dummy_revenue"],
    )


def evidence(evidence_id: str = "evidence_dummy_revenue") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        doc_id="doc_dummy",
        location_type=EvidenceLocationType.TABLE,
    )


@pytest.mark.parametrize("fiscal_period", ["annual", "FY"])
def test_selects_one_annual_consolidated_revenue(fiscal_period: str) -> None:
    selected = select_periodic_fact(
        query_plan(),
        [fact(fiscal_period=fiscal_period)],
        [evidence()],
    )

    assert selected is not None
    assert selected[0].fiscal_year == 2025
    assert selected[1].evidence_id == selected[0].evidence_ids[0]


def test_rejects_different_fiscal_year() -> None:
    assert select_periodic_fact(
        query_plan(),
        [fact(fiscal_year=2024)],
        [evidence()],
    ) is None


@pytest.mark.parametrize(
    "scope",
    [FactAccountingScope.SEPARATE, FactAccountingScope.UNKNOWN],
)
def test_rejects_non_consolidated_scope(scope: FactAccountingScope) -> None:
    assert select_periodic_fact(
        query_plan(),
        [fact(scope=scope)],
        [evidence()],
    ) is None


@pytest.mark.parametrize("fiscal_period", ["quarter", "half-year"])
def test_rejects_non_annual_period(fiscal_period: str) -> None:
    assert select_periodic_fact(
        query_plan(),
        [fact(fiscal_period=fiscal_period)],
        [evidence()],
    ) is None


def test_rejects_two_matching_candidates() -> None:
    assert select_periodic_fact(
        query_plan(),
        [fact(fact_id="fact_a"), fact(fact_id="fact_b")],
        [evidence()],
    ) is None


def test_rejects_missing_linked_evidence() -> None:
    assert select_periodic_fact(query_plan(), [fact()], []) is None
