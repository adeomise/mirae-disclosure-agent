"""Metadata filtering used by the Q01 periodic retrieval slice."""

from typing import Any

from disclosure_agent.common.enums import DocumentClass, EventType
from disclosure_agent.common.schemas import QueryPlan


def matches_periodic_metadata(metadata: dict[str, Any], query_plan: QueryPlan) -> bool:
    """Match company, fiscal year, and an explicit annual-report marker."""
    if not query_plan.entities or not query_plan.periods:
        return False

    company = str(metadata.get("corp_name") or "")
    report_name = str(metadata.get("report_name") or "")
    requested_company = query_plan.entities[0].resolved_name
    requested_year = query_plan.periods[0].year

    company_matches = requested_company.replace(" ", "") in company.replace(" ", "")
    year_matches = requested_year is not None and str(requested_year) in report_name
    normalized_report_name = report_name.replace(" ", "").casefold()
    is_interim_report = any(
        marker in normalized_report_name
        for marker in (
            "\ubc18\uae30\ubcf4\uace0\uc11c",
            "\ubd84\uae30\ubcf4\uace0\uc11c",
            "halfyearreport",
            "quarterlyreport",
        )
    )
    is_annual_report = any(
        marker in normalized_report_name
        for marker in ("\uc0ac\uc5c5\ubcf4\uace0\uc11c", "annualreport", "businessreport")
    )
    return company_matches and year_matches and is_annual_report and not is_interim_report


def matches_event_metadata(metadata: dict[str, Any], query_plan: QueryPlan) -> bool:
    """Match explicit sidecar metadata for one Q02 supply-contract candidate."""
    if (
        query_plan.document_class is not DocumentClass.EVENT
        or query_plan.document_subtype != EventType.SUPPLY_CONTRACT.value
        or not query_plan.entities
        or not query_plan.periods
        or not query_plan.periods[0].range
    ):
        return False

    company = str(metadata.get("corp_name") or "")
    report_name = str(metadata.get("report_name") or "")
    receipt_date = str(metadata.get("receipt_date") or "")
    requested_company = query_plan.entities[0].resolved_name
    normalized_report_name = report_name.replace(" ", "")

    company_matches = requested_company.replace(" ", "") == company.replace(" ", "")
    document_matches = (
        "\ub2e8\uc77c\ud310\ub9e4" in normalized_report_name
        and "\uacf5\uae09\uacc4\uc57d" in normalized_report_name
    )
    period_matches = receipt_date.startswith(query_plan.periods[0].range)
    return company_matches and document_matches and period_matches


def matches_event_history_metadata(
    metadata: dict[str, Any],
    query_plan: QueryPlan,
) -> bool:
    """Select all explicitly identified supply-contract chain candidates."""
    if (
        query_plan.document_class is not DocumentClass.EVENT
        or query_plan.document_subtype != EventType.SUPPLY_CONTRACT.value
        or not query_plan.entities
    ):
        return False

    company = str(metadata.get("corp_name") or "")
    report_name = str(metadata.get("report_name") or "").replace(" ", "")
    requested_company = next(
        (
            entity.resolved_name
            for entity in query_plan.entities
            if entity.role == "company"
        ),
        "",
    )
    return (
        requested_company.replace(" ", "") == company.replace(" ", "")
        and "\ub2e8\uc77c\ud310\ub9e4" in report_name
        and "\uacf5\uae09\uacc4\uc57d" in report_name
    )
