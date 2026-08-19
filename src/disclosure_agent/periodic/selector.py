"""Contract-only selection of periodic facts for targeted questions."""

from disclosure_agent.common.enums import (
    FactAccountingScope,
    MetricConcept,
    QueryAccountingScope,
)
from disclosure_agent.common.schemas import Evidence, Fact, QueryPlan

ANNUAL_PERIODS = {"annual", "fy"}


def select_periodic_fact(
    query_plan: QueryPlan,
    facts: list[Fact],
    evidence: list[Evidence],
) -> tuple[Fact, Evidence] | None:
    """Select one exact annual consolidated fact without inspecting parser output."""
    if (
        query_plan.accounting_scope is not QueryAccountingScope.CONSOLIDATED
        or MetricConcept.REVENUE not in query_plan.metrics
        or not query_plan.periods
        or query_plan.periods[0].year is None
    ):
        return None

    requested_year = query_plan.periods[0].year
    candidates = [
        fact
        for fact in facts
        if fact.metric is MetricConcept.REVENUE
        and fact.fiscal_year == requested_year
        and (fact.fiscal_period or "").casefold() in ANNUAL_PERIODS
        and fact.accounting_scope is FactAccountingScope.CONSOLIDATED
        and fact.raw_value is not None
        and fact.raw_unit is not None
    ]
    if len(candidates) != 1:
        return None

    fact = candidates[0]
    if len(fact.evidence_ids) != 1:
        return None
    linked_evidence = [
        item for item in evidence if item.evidence_id == fact.evidence_ids[0]
    ]
    if len(linked_evidence) != 1:
        return None
    return fact, linked_evidence[0]
