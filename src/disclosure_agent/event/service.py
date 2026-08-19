"""Q02 event retrieval and parser-to-contract adapter."""

from pathlib import Path
from typing import Any

from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ChainPolicy,
    EventLinkResolutionStatus,
    EventType,
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import (
    AnswerResponse,
    Event,
    EventHistory,
    EventLink,
    Evidence,
    Fact,
    QueryPlan,
)
from disclosure_agent.event.diff import (
    diff_supply_contract_documents,
    find_change_row,
)
from disclosure_agent.event.identity import make_event_id
from disclosure_agent.event.linker import (
    has_missing_related_disclosures,
    link_supply_contract_correction,
)
from disclosure_agent.retrieval.service import (
    retrieve_event_documents,
    retrieve_event_history_documents,
)


class EventQ02Service:
    """Answer Q02 only when one unambiguous original supply contract is found."""

    def __init__(self, corpus_dir: str | Path) -> None:
        self.corpus_dir = Path(corpus_dir)

    def answer(self, query_plan: QueryPlan) -> AnswerResponse:
        parsed_documents = retrieve_event_documents(query_plan, self.corpus_dir)
        mapped_results = [
            result
            for document in parsed_documents
            if (result := _map_supply_contract(document, query_plan)) is not None
        ]
        if len(mapped_results) != 1:
            reason = (
                "No matching original supply-contract document was resolved."
                if not mapped_results
                else "Multiple supply-contract documents are ambiguous."
            )
            return AnswerResponse(answer=reason, status=AnswerabilityStatus.UNRESOLVED)

        event, fact, evidence = mapped_results[0]
        return AnswerResponse(
            answer=(
                f"{event.company} supply contract amount: "
                f"{fact.raw_value} {fact.raw_unit}; "
                f"period: {event.attributes['contract_start_date']} "
                f"to {event.attributes['contract_end_date']}"
            ),
            status=AnswerabilityStatus.ANSWERABLE,
            events=[event],
            facts=[fact],
            evidence=evidence,
        )


class EventQ13Service:
    """Build one correction history only from an explicitly resolved chain."""

    def __init__(self, corpus_dir: str | Path) -> None:
        self.corpus_dir = Path(corpus_dir)

    def answer(self, query_plan: QueryPlan) -> AnswerResponse:
        if query_plan.chain_policy is not ChainPolicy.FULL_CHAIN:
            return _unresolved_history(
                "Q13 requires the full-chain policy.",
                AnswerabilityReason.INSUFFICIENT_EVIDENCE,
            )

        documents = retrieve_event_history_documents(query_plan, self.corpus_dir)
        requested_period = query_plan.periods[0].range if query_plan.periods else None
        corrections = sorted(
            (
                document
                for document in documents
                if (document.get("metadata") or {}).get("is_correction") is True
                and requested_period
                and str(
                    (document.get("metadata") or {}).get("receipt_date") or ""
                ).startswith(requested_period)
            ),
            key=_document_sort_key,
        )
        originals = [
            document
            for document in documents
            if (document.get("metadata") or {}).get("is_correction") is False
        ]
        if not corrections:
            return _unresolved_history(
                "No correction disclosure was resolved.",
                AnswerabilityReason.NOT_FOUND,
            )

        links = [
            link_supply_contract_correction(correction, originals)
            for correction in corrections
        ]
        if any(
            link.resolution_status is not EventLinkResolutionStatus.RESOLVED
            for link in links
        ):
            reason = (
                AnswerabilityReason.AMBIGUOUS_LINK
                if any(
                    link.resolution_status is EventLinkResolutionStatus.AMBIGUOUS
                    for link in links
                )
                else AnswerabilityReason.NOT_FOUND
            )
            return _unresolved_history(
                "Correction-to-original link was not resolved.",
                reason,
                links,
            )

        resolved_doc_ids = {link.resolved_doc_id for link in links}
        if len(resolved_doc_ids) != 1:
            return _unresolved_history(
                "Multiple event chains match the question.",
                AnswerabilityReason.AMBIGUOUS_LINK,
                links,
            )
        original_doc_id = next(iter(resolved_doc_ids))
        original = next(
            (
                document
                for document in originals
                if (document.get("metadata") or {}).get("doc_id")
                == original_doc_id
            ),
            None,
        )
        if original is None or not _matches_requested_counterparty(
            original,
            query_plan,
        ):
            return _unresolved_history(
                "The referenced original event was not resolved.",
                AnswerabilityReason.NOT_FOUND,
                links,
            )

        chain_documents = [original, *corrections]
        events = [
            _map_history_event(document, original_doc_id)
            for document in chain_documents
        ]
        if any(event is None for event in events):
            return _unresolved_history(
                "A required event field is missing.",
                AnswerabilityReason.INSUFFICIENT_EVIDENCE,
                links,
            )

        changes = []
        evidence_by_id: dict[str, Evidence] = {}
        previous = original
        for correction in corrections:
            document_changes = diff_supply_contract_documents(previous, correction)
            for change in document_changes:
                change_evidence: dict[str, Evidence] = {}
                for document, version in (
                    (previous, "before"),
                    (correction, "after"),
                ):
                    evidence = _build_history_evidence(
                        document,
                        change.field_name,
                        version,
                    )
                    if evidence is not None:
                        evidence_by_id[evidence.evidence_id] = evidence
                        change_evidence[version] = evidence
                changes.append(
                    change.model_copy(
                        update={
                            "before_evidence_id": (
                                change_evidence.get("before").evidence_id
                                if change_evidence.get("before")
                                else None
                            ),
                            "after_evidence_id": (
                                change_evidence.get("after").evidence_id
                                if change_evidence.get("after")
                                else None
                            ),
                        }
                    )
                )
            previous = correction

        explicit_complete = all(
            (document.get("metadata") or {}).get("chain_complete") is True
            for document in corrections
        )
        missing_related = any(
            has_missing_related_disclosures(document, documents)
            for document in corrections
        )
        chain_complete = explicit_complete and not missing_related
        history = EventHistory(
            event_id=make_event_id(EventType.SUPPLY_CONTRACT, original_doc_id),
            document_ids=[
                str((document.get("metadata") or {}).get("doc_id"))
                for document in chain_documents
            ],
            changes=changes,
            chain_complete=chain_complete,
        )
        status = (
            AnswerabilityStatus.ANSWERABLE
            if chain_complete
            else AnswerabilityStatus.PARTIALLY_ANSWERABLE
        )
        answer = (
            "Complete supply-contract correction history resolved."
            if chain_complete
            else "INCOMPLETE_CHAIN: verified changes are partial."
        )
        return AnswerResponse(
            answer=answer,
            status=status,
            reason=(
                None
                if chain_complete
                else AnswerabilityReason.INCOMPLETE_CHAIN
            ),
            events=[event for event in events if event is not None],
            event_links=links,
            event_histories=[history],
            evidence=list(evidence_by_id.values()),
        )


def _unresolved_history(
    answer: str,
    reason: AnswerabilityReason,
    event_links: list[EventLink] | None = None,
) -> AnswerResponse:
    return AnswerResponse(
        answer=answer,
        status=AnswerabilityStatus.UNRESOLVED,
        reason=reason,
        event_links=event_links or [],
    )


def _document_sort_key(document: dict[str, Any]) -> tuple[str, str]:
    metadata = document.get("metadata") or {}
    return (
        str(metadata.get("receipt_date") or ""),
        str(metadata.get("doc_id") or ""),
    )


def _matches_requested_counterparty(
    document: dict[str, Any],
    query_plan: QueryPlan,
) -> bool:
    requested = next(
        (
            entity.resolved_name
            for entity in query_plan.entities
            if entity.role == "counterparty"
        ),
        None,
    )
    if requested is None:
        return True
    row = _find_row(
        document.get("rows") or [],
        {"\uacc4\uc57d\uc0c1\ub300", "\uacc4\uc57d\uc0c1\ub300\ubc29"},
    )
    return row is not None and _compact(str(row["value"])) == _compact(requested)


def _map_history_event(
    document: dict[str, Any],
    root_doc_id: str,
) -> Event | None:
    metadata = document.get("metadata") or {}
    title = str(document.get("title") or "").replace(" ", "")
    if "\ub2e8\uc77c\ud310\ub9e4" not in title or "\uacf5\uae09\uacc4\uc57d" not in title:
        return None

    rows = document.get("rows") or []
    amount_row = _find_row(rows, {"\uacc4\uc57d\uae08\uc561"})
    start_row = _find_row(
        rows,
        {"\uc2dc\uc791\uc77c"},
        required_group="\uacc4\uc57d\uae30\uac04",
    )
    end_row = _find_row(
        rows,
        {"\uc885\ub8cc\uc77c"},
        required_group="\uacc4\uc57d\uae30\uac04",
    )
    counterparty_row = _find_row(
        rows,
        {"\uacc4\uc57d\uc0c1\ub300", "\uacc4\uc57d\uc0c1\ub300\ubc29"},
    )
    if not all((amount_row, start_row, end_row, counterparty_row)):
        return None

    doc_id = str(metadata.get("doc_id") or "")
    company = str(metadata.get("corp_name") or "")
    is_correction = metadata.get("is_correction")
    if not doc_id or not company or not isinstance(is_correction, bool):
        return None
    return Event(
        event_id=make_event_id(EventType.SUPPLY_CONTRACT, root_doc_id),
        event_type=EventType.SUPPLY_CONTRACT,
        company=company,
        source_doc_id=doc_id,
        attributes={
            "contract_amount": str(amount_row["value"]),
            "contract_amount_unit": amount_row.get("unit"),
            "contract_start_date": str(start_row["value"]),
            "contract_end_date": str(end_row["value"]),
            "counterparty": str(counterparty_row["value"]),
        },
        is_correction=is_correction,
    )


def _build_history_evidence(
    document: dict[str, Any],
    field_name: str,
    version: str,
) -> Evidence | None:
    row = find_change_row(document, field_name)
    metadata = document.get("metadata") or {}
    doc_id = str(metadata.get("doc_id") or "")
    if row is None or not doc_id:
        return None
    group = str(row.get("group") or "")
    raw_field = str(row.get("raw_field") or row.get("field") or "")
    raw_value = str(row.get("value") or "")
    return Evidence(
        evidence_id=f"evidence_{doc_id}_{field_name}_{version}",
        doc_id=doc_id,
        location_type=EvidenceLocationType.EVENT_FIELD,
        section_path=[group] if group else [],
        field_name=field_name,
        raw_text=f"{raw_field}: {raw_value}",
    )


def _map_supply_contract(
    document: dict[str, Any],
    query_plan: QueryPlan,
) -> tuple[Event, Fact, list[Evidence]] | None:
    metadata = document.get("metadata") or {}
    title = str(document.get("title") or "").replace(" ", "")
    if (
        "\ub2e8\uc77c\ud310\ub9e4" not in title
        or "\uacf5\uae09\uacc4\uc57d" not in title
        or metadata.get("is_correction") is not False
    ):
        return None

    rows = document.get("rows") or []
    amount_row = _find_row(rows, {"\uacc4\uc57d\uae08\uc561"})
    start_row = _find_row(rows, {"\uc2dc\uc791\uc77c"}, required_group="\uacc4\uc57d\uae30\uac04")
    end_row = _find_row(rows, {"\uc885\ub8cc\uc77c"}, required_group="\uacc4\uc57d\uae30\uac04")
    counterparty_row = _find_row(
        rows,
        {"\uacc4\uc57d\uc0c1\ub300", "\uacc4\uc57d\uc0c1\ub300\ubc29"},
    )
    if not all((amount_row, start_row, end_row, counterparty_row)):
        return None

    requested_counterparty = next(
        (
            entity.resolved_name
            for entity in query_plan.entities
            if entity.role == "counterparty"
        ),
        None,
    )
    parsed_counterparty = str(counterparty_row["value"])
    if (
        requested_counterparty is None
        or _compact(requested_counterparty) != _compact(parsed_counterparty)
    ):
        return None

    raw_amount = str(amount_row["value"])
    raw_unit = amount_row.get("unit")
    doc_id = str(metadata.get("doc_id") or "")
    company = str(metadata.get("corp_name") or "")
    if not raw_amount or not raw_unit or not doc_id or not company:
        return None

    field_rows = {
        "contract_amount": amount_row,
        "contract_start_date": start_row,
        "contract_end_date": end_row,
        "counterparty": counterparty_row,
    }
    evidence = [
        _build_evidence(doc_id, field_name, row)
        for field_name, row in field_rows.items()
    ]
    amount_evidence_id = evidence[0].evidence_id
    event = Event(
        event_id=make_event_id(EventType.SUPPLY_CONTRACT, doc_id),
        event_type=EventType.SUPPLY_CONTRACT,
        company=company,
        source_doc_id=doc_id,
        attributes={
            "contract_amount": raw_amount,
            "contract_start_date": str(start_row["value"]),
            "contract_end_date": str(end_row["value"]),
            "counterparty": parsed_counterparty,
        },
        is_correction=False,
    )
    fact = Fact(
        fact_id=f"fact_{doc_id}_contract_amount",
        company=company,
        metric=MetricConcept.CONTRACT_AMOUNT,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
        raw_value=raw_amount,
        raw_unit=str(raw_unit),
        evidence_ids=[amount_evidence_id],
    )
    return event, fact, evidence


def _find_row(
    rows: list[dict[str, Any]],
    field_names: set[str],
    required_group: str | None = None,
) -> dict[str, Any] | None:
    for row in rows:
        if _compact(str(row.get("field") or "")) not in field_names:
            continue
        if required_group and _compact(str(row.get("group") or "")) != required_group:
            continue
        if row.get("value"):
            return row
    return None


def _build_evidence(
    doc_id: str,
    field_name: str,
    row: dict[str, Any],
) -> Evidence:
    group = str(row.get("group") or "")
    raw_field = str(row.get("raw_field") or row.get("field") or "")
    raw_value = str(row.get("value") or "")
    return Evidence(
        evidence_id=f"evidence_{doc_id}_{field_name}",
        doc_id=doc_id,
        location_type=EvidenceLocationType.EVENT_FIELD,
        section_path=[group] if group else [],
        field_name=field_name,
        raw_text=f"{raw_field}: {raw_value}",
    )


def _compact(value: str) -> str:
    return "".join(value.split())
