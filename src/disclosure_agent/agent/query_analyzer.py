"""Deterministic query analysis for the Q01 vertical slice."""

import re

from disclosure_agent.common.enums import (
    ChainPolicy,
    DocumentClass,
    EventType,
    Intent,
    MetricConcept,
    QueryAccountingScope,
)
from disclosure_agent.common.schemas import Entity, Period, QueryPlan

Q01_COMPANY = "\ud55c\ubbf8\ubc18\ub3c4\uccb4"
Q02_COMPANY = "\ud55c\ud654\uc624\uc158"
Q02_COUNTERPARTY = "\uc591\ubc0d\ud574\uc6b4"
YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2})(?:\ub144)?")
YEAR_MONTH_PATTERN = re.compile(r"(?<!\d)(20\d{2})\ub144\s*(1[0-2]|0?[1-9])\uc6d4")


def analyze_q01_question(question: str) -> QueryPlan | None:
    """Return a QueryPlan only for the currently supported Q01 shape."""
    year_match = YEAR_PATTERN.search(question)
    has_company = Q01_COMPANY in question
    has_consolidated_scope = "\uc5f0\uacb0" in question or "consolidated" in question.casefold()
    has_revenue = (
        "\ub9e4\ucd9c\uc561" in question
        or "\ub9e4\ucd9c" in question
        or "revenue" in question.casefold()
    )

    if not (year_match and has_company and has_consolidated_scope and has_revenue):
        return None

    fiscal_year = int(year_match.group(1))
    return QueryPlan(
        query_id=f"q01-{Q01_COMPANY}-{fiscal_year}-consolidated-revenue",
        original_query=question,
        intent=Intent.SINGLE_FACT,
        entities=[
            Entity(
                surface=Q01_COMPANY,
                resolved_name=Q01_COMPANY,
                role="company",
            )
        ],
        periods=[Period(kind="fiscal_year", year=fiscal_year, period_type="FY")],
        document_class=DocumentClass.PERIODIC,
        accounting_scope=QueryAccountingScope.CONSOLIDATED,
        metrics=[MetricConcept.REVENUE],
    )


def analyze_q02_question(question: str) -> QueryPlan | None:
    """Return a QueryPlan only for the supported Q02 supply-contract shape."""
    period_match = YEAR_MONTH_PATTERN.search(question)
    has_company = Q02_COMPANY in question
    has_counterparty = Q02_COUNTERPARTY in question
    has_supply_contract = (
        "\uacf5\uae09\uacc4\uc57d" in question
        or "supply contract" in question.casefold()
    )

    if not (period_match and has_company and has_counterparty and has_supply_contract):
        return None

    year = int(period_match.group(1))
    month = int(period_match.group(2))
    return QueryPlan(
        query_id=f"q02-{Q02_COMPANY}-{year}-{month:02d}-supply-contract",
        original_query=question,
        intent=Intent.SINGLE_FACT,
        entities=[
            Entity(surface=Q02_COMPANY, resolved_name=Q02_COMPANY, role="company"),
            Entity(
                surface=Q02_COUNTERPARTY,
                resolved_name=Q02_COUNTERPARTY,
                role="counterparty",
            ),
        ],
        periods=[
            Period(
                kind="filing",
                year=year,
                period_type="month",
                range=f"{year}-{month:02d}",
            )
        ],
        document_class=DocumentClass.EVENT,
        document_subtype=EventType.SUPPLY_CONTRACT.value,
        accounting_scope=QueryAccountingScope.NOT_APPLICABLE,
        metrics=[
            MetricConcept.CONTRACT_AMOUNT,
            MetricConcept.CONTRACT_START_DATE,
            MetricConcept.CONTRACT_END_DATE,
        ],
    )


def analyze_q13_question(question: str) -> QueryPlan | None:
    """Return a QueryPlan only for the supported Q13 correction-history shape."""
    year_match = YEAR_PATTERN.search(question)
    has_company = Q02_COMPANY in question
    has_supply_contract = (
        "\uacf5\uae09\uacc4\uc57d" in question
        or "supply contract" in question.casefold()
    )
    has_history = (
        "\uc815\uc815" in question
        or "\ubcc0\uacbd" in question
        or "correction" in question.casefold()
    )
    if not (year_match and has_company and has_supply_contract and has_history):
        return None

    year = int(year_match.group(1))
    entities = [
        Entity(surface=Q02_COMPANY, resolved_name=Q02_COMPANY, role="company")
    ]
    if Q02_COUNTERPARTY in question:
        entities.append(
            Entity(
                surface=Q02_COUNTERPARTY,
                resolved_name=Q02_COUNTERPARTY,
                role="counterparty",
            )
        )
    return QueryPlan(
        query_id=f"q13-{Q02_COMPANY}-{year}-supply-contract-history",
        original_query=question,
        intent=Intent.EVENT_HISTORY,
        entities=entities,
        periods=[
            Period(
                kind="filing_year",
                year=year,
                period_type="year",
                range=str(year),
            )
        ],
        document_class=DocumentClass.EVENT,
        document_subtype=EventType.SUPPLY_CONTRACT.value,
        accounting_scope=QueryAccountingScope.NOT_APPLICABLE,
        metrics=[
            MetricConcept.CONTRACT_AMOUNT,
            MetricConcept.CONTRACT_START_DATE,
            MetricConcept.CONTRACT_END_DATE,
        ],
        chain_policy=ChainPolicy.FULL_CHAIN,
    )
