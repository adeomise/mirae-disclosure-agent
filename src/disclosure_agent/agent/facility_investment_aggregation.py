"""Q12용 시설투자 이벤트 집합·최신 금액 확보.

대상 연도는 non-correction root 집합만 정한다. 각 root의 금액은 연도 제한
없이 전체 정정 chain을 따라간 뒤 기존 latest-state resolver가 확정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from disclosure_agent.common.enums import EventType, FactAccountingScope, MetricConcept
from disclosure_agent.common.schemas import ConversionGap, Evidence, Fact
from disclosure_agent.event.adapter import EventAdapterResult, adapt_extracted_event
from disclosure_agent.event.contract_adapter import to_event_history
from disclosure_agent.event.exceptions import UnsupportedCompositeCorrectionError
from disclosure_agent.event.extractor import EVENT_FACILITY_INVESTMENT, extract_event
from disclosure_agent.event.latest_state import resolve_latest_event_state
from disclosure_agent.event.linker import (
    LINK_STATUS_AMBIGUOUS_LINK,
    FilingChain,
    build_filing_chain,
)
from disclosure_agent.event.parser import parse_exchange_document
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    FilingRef,
    list_event_filings,
)

FACILITY_INVESTMENT_SUBTYPE = "신규시설투자등"
INVESTMENT_AMOUNT_FIELD = "investment_amount"
INVESTMENT_AMOUNT_UNIT = "KRW"

STAGE_FILING_DISCOVERY = "filing_discovery"
STAGE_PARSING = "parsing"
STAGE_EXTRACTION = "extraction"
STAGE_LATEST_STATE_RESOLUTION = "latest_state_resolution"
STAGE_EVIDENCE = "evidence"
STAGE_COMPARABILITY = "comparability"

REASON_NO_MATCHING_FILING = "no_matching_filing"
REASON_PARSE_FAILED = "parse_failed"
REASON_EXTRACTION_FAILED = "event_extraction_failed"
REASON_INCOMPLETE_CHAIN = "incomplete_chain"
REASON_AMBIGUOUS_LINK = "ambiguous_link"
REASON_UNSUPPORTED_COMPOSITE_CORRECTION = "unsupported_composite_correction"
REASON_LATEST_STATE_FAILED = "latest_state_resolution_failed"
REASON_MISSING_INVESTMENT_AMOUNT = "missing_investment_amount"
REASON_INCOMPATIBLE_UNIT = "incompatible_unit"
REASON_MISSING_PROVENANCE = "missing_latest_evidence"

GAP_EXCLUDED_EVENT = "excluded_facility_investment_event"


@dataclass(frozen=True)
class FacilityInvestmentFailure:
    """집계에 들이지 못한 root 이벤트 하나."""

    company: str
    root_doc_id: str
    stage: str
    reason: str
    detail: str

    def to_gap(self) -> ConversionGap:
        return ConversionGap(
            kind=GAP_EXCLUDED_EVENT,
            subject=self.root_doc_id or self.company,
            dropped={
                "company": self.company,
                "root_doc_id": self.root_doc_id,
                "stage": self.stage,
                "reason": self.reason,
            },
            detail=self.detail,
        )


@dataclass(frozen=True)
class FacilityInvestmentCollection:
    """한 기업의 Q12 root 집합을 필드 단위 최신 Fact로 옮긴 결과."""

    company: str
    root_doc_ids: tuple[str, ...] = field(default_factory=tuple)
    facts: tuple[Fact, ...] = field(default_factory=tuple)
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    failures: tuple[FacilityInvestmentFailure, ...] = field(default_factory=tuple)


def _doc_id(filing: FilingRef) -> str:
    return filing.doc_id or filing.receipt_no


def _failure(
    *, company: str, root_doc_id: str, stage: str, reason: str, detail: str
) -> FacilityInvestmentFailure:
    return FacilityInvestmentFailure(
        company=company,
        root_doc_id=root_doc_id,
        stage=stage,
        reason=reason,
        detail=detail,
    )


def _chain_failure(company: str, root: FilingRef, chain: FilingChain) -> FacilityInvestmentFailure:
    ambiguous = any(
        link.status == LINK_STATUS_AMBIGUOUS_LINK for link in chain.unresolved_links
    )
    reason = REASON_AMBIGUOUS_LINK if ambiguous else REASON_INCOMPLETE_CHAIN

    return _failure(
        company=company,
        root_doc_id=_doc_id(root),
        stage=STAGE_LATEST_STATE_RESOLUTION,
        reason=reason,
        detail=(
            f"{_doc_id(root)}의 정정 chain을 완결하지 못해 최신 투자금액을 "
            "확정하지 않았습니다. 원공시 금액으로 대신하지 않습니다."
        ),
    )


def _exact_numeric_text(value: str | float | None) -> str | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

    if not number.is_finite():
        return None

    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return "0" if number.is_zero() else text


def _extract_filing_event(filing: FilingRef, document: dict) -> dict:
    """Parser 본문과 manifest의 확정 메타데이터를 Extractor 결과에 결합한다."""
    extracted = extract_event(document)

    # XML 본문에 회사명이 없는 문서가 있다. 질문에서 추측하지 않고,
    # 해당 문서를 선택한 production manifest FilingRef의 기업 정체성을 사용한다.
    if not extracted.get("company"):
        extracted = {**extracted, "company": filing.corp_name}

    return extracted


def _fact_from_latest_state(
    *,
    company: str,
    root: FilingRef,
    state,
    adapted_by_doc_id: dict[str, EventAdapterResult],
    extracted_by_doc_id: dict[str, dict],
) -> tuple[Fact | None, tuple[Evidence, ...], FacilityInvestmentFailure | None]:
    root_doc_id = _doc_id(root)
    amount = state.fields.get(INVESTMENT_AMOUNT_FIELD)

    if amount is None or _exact_numeric_text(amount.value) is None:
        return None, (), _failure(
            company=company,
            root_doc_id=root_doc_id,
            stage=STAGE_EXTRACTION,
            reason=REASON_MISSING_INVESTMENT_AMOUNT,
            detail=f"{root_doc_id}의 latest state에 사용 가능한 investment_amount가 없습니다.",
        )

    if amount.unit != INVESTMENT_AMOUNT_UNIT:
        return None, (), _failure(
            company=company,
            root_doc_id=root_doc_id,
            stage=STAGE_COMPARABILITY,
            reason=REASON_INCOMPATIBLE_UNIT,
            detail=(
                f"{root_doc_id}의 investment_amount 정규화 단위가 "
                f"{INVESTMENT_AMOUNT_UNIT}가 아닙니다: {amount.unit!r}."
            ),
        )

    source = adapted_by_doc_id.get(amount.source_doc_id)
    extracted_source = extracted_by_doc_id.get(amount.source_doc_id)
    evidence_by_id = {
        item.evidence_id: item
        for result in adapted_by_doc_id.values()
        for item in result.evidence
    }
    resolved_evidence = tuple(
        evidence_by_id[evidence_id]
        for evidence_id in amount.evidence_ids
        if evidence_id in evidence_by_id
    )

    if (
        not amount.source_doc_id
        or source is None
        or extracted_source is None
        or not amount.evidence_ids
        or len(resolved_evidence) != len(amount.evidence_ids)
        or any(item.doc_id != amount.source_doc_id for item in resolved_evidence)
    ):
        return None, (), _failure(
            company=company,
            root_doc_id=root_doc_id,
            stage=STAGE_EVIDENCE,
            reason=REASON_MISSING_PROVENANCE,
            detail=(
                f"{root_doc_id}의 latest investment_amount 근거가 source document에서 "
                "전부 resolve되지 않습니다. 근거 없이 Fact를 만들지 않습니다."
            ),
        )

    raw_field = (extracted_source.get("fields") or {}).get(INVESTMENT_AMOUNT_FIELD) or {}

    return (
        Fact(
            fact_id=f"fact_{state.event_id}_{INVESTMENT_AMOUNT_FIELD}",
            company=company,
            metric=MetricConcept.INVESTMENT_AMOUNT,
            fiscal_year=None,
            fiscal_period=None,
            accounting_scope=FactAccountingScope.NOT_APPLICABLE,
            raw_value=(
                str(raw_field.get("raw_value"))
                if raw_field.get("raw_value") is not None
                else None
            ),
            raw_unit=(
                str(raw_field.get("raw_unit"))
                if raw_field.get("raw_unit") is not None
                else None
            ),
            normalized_value=_exact_numeric_text(amount.value),
            normalized_unit=INVESTMENT_AMOUNT_UNIT,
            evidence_ids=list(amount.evidence_ids),
        ),
        resolved_evidence,
        None,
    )


def collect_facility_investment_facts(
    *,
    company: str,
    target_year: int,
    corpus_root: str | Path,
    manifest_path: str | Path | None = None,
) -> FacilityInvestmentCollection:
    """대상 연도 root를 모두 독립적으로 resolve해 건별 Fact를 만든다."""
    corpus_root = Path(corpus_root)
    manifest_path = Path(manifest_path) if manifest_path else corpus_root / "manifest.jsonl"
    retrieval = {
        "manifest_path": manifest_path,
        "corpus_root": corpus_root,
        "company": company,
        "document_subtype": FACILITY_INVESTMENT_SUBTYPE,
    }

    try:
        roots = list_event_filings(
            **retrieval,
            include_corrections=False,
            year=target_year,
        )
        chain_candidates = list_event_filings(
            **retrieval,
            include_corrections=True,
        )
    except FilingNotFoundError as error:
        failure = _failure(
            company=company,
            root_doc_id="",
            stage=STAGE_FILING_DISCOVERY,
            reason=error.reason,
            detail=error.detail,
        )
        return FacilityInvestmentCollection(company=company, failures=(failure,))

    if not roots:
        failure = _failure(
            company=company,
            root_doc_id="",
            stage=STAGE_FILING_DISCOVERY,
            reason=REASON_NO_MATCHING_FILING,
            detail=(
                f"{company}의 {target_year}년 신규시설투자 non-correction root가 "
                "없습니다. 다른 연도 root로 대신하지 않습니다."
            ),
        )
        return FacilityInvestmentCollection(company=company, failures=(failure,))

    root_doc_ids = tuple(_doc_id(root) for root in roots)
    root_receipt_nos = {root.receipt_no for root in roots}

    # Linker에는 전체 candidate 집합을 그대로 넘긴다. 정정이 가리킨 원공시
    # 후보를 빼면 애매한 연결이 거짓으로 유일해질 수 있기 때문이다. 다만 parse는
    # target root와 연도 제한 없는 correction에만 필요하다. 다른 root로 확정된
    # non-correction 문서는 메타데이터만으로 original 후보가 되므로 본문을 읽지 않는다.
    parse_candidates = tuple(
        filing
        for filing in chain_candidates
        if filing.is_correction or filing.receipt_no in root_receipt_nos
    )

    try:
        documents = {
            filing.receipt_no: parse_exchange_document(filing.main_document)
            for filing in parse_candidates
        }
    except (OSError, ValueError) as error:
        failure = _failure(
            company=company,
            root_doc_id="",
            stage=STAGE_PARSING,
            reason=REASON_PARSE_FAILED,
            detail=str(error),
        )
        return FacilityInvestmentCollection(
            company=company, root_doc_ids=root_doc_ids, failures=(failure,)
        )

    # 모든 correction을 parse한 뒤 target root별 chain을 먼저 확정한다. filings에는
    # 전체 candidate를 넘겨 original disambiguation을 약화하지 않는다.
    chains_by_root = {
        root.receipt_no: build_filing_chain(
            root_receipt_no=root.receipt_no,
            filings=chain_candidates,
            documents=documents,
            event_type=EVENT_FACILITY_INVESTMENT,
        )
        for root in roots
    }
    target_chain_receipt_nos = {
        entry.filing.receipt_no
        for chain in chains_by_root.values()
        for entry in chain.entries
    }
    extraction_candidates = tuple(
        filing
        for filing in parse_candidates
        if filing.receipt_no in target_chain_receipt_nos
    )

    try:
        extracted_by_receipt = {
            filing.receipt_no: _extract_filing_event(
                filing, documents[filing.receipt_no]
            )
            for filing in extraction_candidates
        }
        extracted_by_doc_id = {
            str(extracted["doc_id"]): extracted
            for extracted in extracted_by_receipt.values()
        }
        adapted_by_receipt = {
            receipt_no: adapt_extracted_event(extracted)
            for receipt_no, extracted in extracted_by_receipt.items()
        }
        adapted_by_doc_id = {
            result.event.source_doc_id: result for result in adapted_by_receipt.values()
        }
    except (KeyError, ValueError) as error:
        failure = _failure(
            company=company,
            root_doc_id="",
            stage=STAGE_EXTRACTION,
            reason=REASON_EXTRACTION_FAILED,
            detail=str(error),
        )
        return FacilityInvestmentCollection(
            company=company, root_doc_ids=root_doc_ids, failures=(failure,)
        )

    facts: list[Fact] = []
    evidence: list[Evidence] = []
    failures: list[FacilityInvestmentFailure] = []

    for root in roots:
        chain = chains_by_root[root.receipt_no]

        if not chain.is_complete:
            failures.append(_chain_failure(company, root, chain))
            continue

        conversion = to_event_history(
            chain,
            documents=documents,
            filings=chain_candidates,
            event_type=EventType.FACILITY_INVESTMENT,
        )

        if conversion.history is None or not conversion.history.chain_complete:
            failures.append(_chain_failure(company, root, chain))
            continue

        root_result = adapted_by_receipt[root.receipt_no]

        try:
            state = resolve_latest_event_state(
                root_event=root_result.event,
                history=conversion.history,
                chain=chain,
                documents=adapted_by_doc_id,
            )
        except UnsupportedCompositeCorrectionError as error:
            failures.append(
                _failure(
                    company=company,
                    root_doc_id=_doc_id(root),
                    stage=STAGE_LATEST_STATE_RESOLUTION,
                    reason=REASON_UNSUPPORTED_COMPOSITE_CORRECTION,
                    detail=str(error),
                )
            )
            continue
        except ValueError as error:
            failures.append(
                _failure(
                    company=company,
                    root_doc_id=_doc_id(root),
                    stage=STAGE_LATEST_STATE_RESOLUTION,
                    reason=REASON_LATEST_STATE_FAILED,
                    detail=str(error),
                )
            )
            continue

        fact, event_evidence, failure = _fact_from_latest_state(
            company=company,
            root=root,
            state=state,
            adapted_by_doc_id=adapted_by_doc_id,
            extracted_by_doc_id=extracted_by_doc_id,
        )

        if failure is not None:
            failures.append(failure)
            continue

        facts.append(fact)
        evidence.extend(event_evidence)

    unique_evidence = tuple(
        {item.evidence_id: item for item in evidence}.values()
    )

    return FacilityInvestmentCollection(
        company=company,
        root_doc_ids=root_doc_ids,
        facts=tuple(facts),
        evidence=unique_evidence,
        failures=tuple(failures),
    )
