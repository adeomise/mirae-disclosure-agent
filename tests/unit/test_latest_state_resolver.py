"""Latest-state resolver unit and actual-corpus regressions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from disclosure_agent.common.enums import EventType, EvidenceLocationType
from disclosure_agent.common.schemas import Event, EventHistory, Evidence
from disclosure_agent.event.adapter import EventAdapterResult, adapt_extracted_event
from disclosure_agent.event.contract_adapter import to_event_history
from disclosure_agent.event.diff import FieldChange
from disclosure_agent.event.extractor import extract_event
from disclosure_agent.event.latest_state import resolve_latest_event_state
from disclosure_agent.event.linker import ChainEntry, FilingChain, build_filing_chain
from disclosure_agent.event.parser import parse_exchange_document

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
DEFAULT_EXCHANGE_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "exchange"
)
SAMSUNG_COMPANY = "삼성바이오로직스"
SAMSUNG_FILINGS = (
    ("20230317800146", "2023-03-17", False),
    ("20230605800274", "2023-06-05", True),
    ("20241218800350", "2024-12-18", True),
)


@dataclass(frozen=True)
class FilingStub:
    receipt_no: str
    receipt_date: str
    doc_id: str
    corp_name: str = "테스트바이오"
    document_subtype: str = "신규시설투자등"
    is_correction: bool = False


def _result(
    doc_id: str,
    *,
    attributes: dict[str, object],
    is_correction: bool = False,
    company: str = "테스트바이오",
) -> EventAdapterResult:
    event = Event(
        event_id=f"event_{doc_id}",
        event_type=EventType.FACILITY_INVESTMENT,
        company=company,
        source_doc_id=doc_id,
        attributes=attributes,
        is_correction=is_correction,
    )
    evidence = [
        Evidence(
            evidence_id=f"evidence_{doc_id}_{field_name}",
            doc_id=doc_id,
            location_type=EvidenceLocationType.EVENT_FIELD,
            field_name=field_name,
        )
        for field_name in attributes
        if not field_name.endswith("_unit")
    ]
    return EventAdapterResult(event=event, evidence=evidence, field_changes=[])


def _synthetic_inputs():
    root = FilingStub("ROOT", "2023-01-01", "exchange_ROOT")
    first = FilingStub(
        "FIRST",
        "2023-06-01",
        "exchange_FIRST",
        is_correction=True,
    )
    second = FilingStub(
        "SECOND",
        "2024-01-01",
        "exchange_SECOND",
        is_correction=True,
    )

    chain = FilingChain(
        root_receipt_no=root.receipt_no,
        entries=(
            ChainEntry(
                filing=second,
                is_correction=True,
                changes=(
                    FieldChange(
                        "investment_amount",
                        "투자금액",
                        "100",
                        "200",
                    ),
                    FieldChange(
                        None,
                        "지원하지 않는 필드",
                        "A",
                        "B",
                    ),
                ),
            ),
            ChainEntry(
                filing=root,
                is_correction=False,
            ),
            ChainEntry(
                filing=first,
                is_correction=True,
                changes=(
                    FieldChange(
                        "investment_end_date",
                        "종료일",
                        "old",
                        "new",
                    ),
                ),
            ),
        ),
    )

    documents = {
        root.doc_id: _result(
            root.doc_id,
            attributes={
                "investment_amount": 100,
                "investment_amount_unit": "KRW",
                "investment_end_date": "2025-09-30",
                "investment_target": "합성 생산시설",
            },
        ),
        first.doc_id: _result(
            first.doc_id,
            attributes={
                "investment_end_date": "2025-04-01",
            },
            is_correction=True,
        ),
        second.doc_id: _result(
            second.doc_id,
            attributes={
                "investment_amount": 200,
                "investment_amount_unit": "KRW",
                "investment_end_date": "본문 snapshot은 provenance 근거가 아님",
            },
            is_correction=True,
        ),
    }

    history = EventHistory(
        event_id="event_facility_investment_exchange_ROOT",
        document_ids=[
            root.doc_id,
            first.doc_id,
            second.doc_id,
        ],
        changes=[],
        chain_complete=True,
    )

    return documents[root.doc_id].event, history, chain, documents


def test_resolves_each_field_from_its_explicit_change_source() -> None:
    root_event, history, chain, documents = _synthetic_inputs()

    state = resolve_latest_event_state(
        root_event=root_event,
        history=history,
        chain=chain,
        documents=documents,
    )

    assert state.as_of.isoformat() == "2024-01-01"

    assert state.fields["investment_amount"].value == 200
    assert state.fields["investment_amount"].unit == "KRW"
    assert (
        state.fields["investment_amount"].source_doc_id
        == "exchange_SECOND"
    )

    assert state.fields["investment_end_date"].value == "2025-04-01"
    assert (
        state.fields["investment_end_date"].source_doc_id
        == "exchange_FIRST"
    )

    assert state.fields["investment_target"].value == "합성 생산시설"
    assert (
        state.fields["investment_target"].source_doc_id
        == "exchange_ROOT"
    )

    assert "지원하지 않는 필드" not in state.fields


def test_missing_normalized_correction_field_fails_closed() -> None:
    root_event, history, chain, documents = _synthetic_inputs()
    documents["exchange_SECOND"].event.attributes.pop("investment_amount")

    with pytest.raises(
        ValueError,
        match="investment_amount.*absent",
    ):
        resolve_latest_event_state(
            root_event=root_event,
            history=history,
            chain=chain,
            documents=documents,
        )


def test_mismatched_event_history_identity_fails_closed() -> None:
    root_event, history, chain, documents = _synthetic_inputs()

    mismatched_history = history.model_copy(
        update={
            "event_id": "event_facility_investment_exchange_WRONG",
        }
    )

    with pytest.raises(
        ValueError,
        match="EventHistory event_id does not match the FilingChain root",
    ):
        resolve_latest_event_state(
            root_event=root_event,
            history=mismatched_history,
            chain=chain,
            documents=documents,
        )


def _actual_exchange_root() -> Path | None:
    configured = os.environ.get(CORPUS_ROOT_ENV)

    if configured:
        root = Path(configured)
        candidates = [
            root,
            root / "raw" / "exchange",
            root / "exchange",
        ]
    else:
        candidates = [
            DEFAULT_EXCHANGE_ROOT,
        ]

    for candidate in candidates:
        if all(
            (candidate / f"{receipt_no}.xml").is_file()
            for receipt_no, _, _ in SAMSUNG_FILINGS
        ):
            return candidate

    return None


def test_samsung_biologics_actual_facility_investment_latest_state() -> None:
    corpus_root = _actual_exchange_root()

    if corpus_root is None:
        pytest.skip(
            f"실측 코퍼스가 없습니다. "
            f"{CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다."
        )

    filings = [
        FilingStub(
            receipt_no=receipt_no,
            receipt_date=receipt_date,
            doc_id=f"exchange_{receipt_no}",
            corp_name=SAMSUNG_COMPANY,
            is_correction=is_correction,
        )
        for receipt_no, receipt_date, is_correction in SAMSUNG_FILINGS
    ]

    parsed = {
        filing.receipt_no: parse_exchange_document(
            corpus_root / f"{filing.receipt_no}.xml",
            corp_name=SAMSUNG_COMPANY,
            receipt_date=filing.receipt_date,
        )
        for filing in filings
    }

    adapted = {
        filing.doc_id: adapt_extracted_event(
            extract_event(parsed[filing.receipt_no])
        )
        for filing in filings
    }

    chain = build_filing_chain(
        root_receipt_no=SAMSUNG_FILINGS[0][0],
        filings=list(reversed(filings)),
        documents=parsed,
        event_type=EventType.FACILITY_INVESTMENT.value,
    )

    history = to_event_history(
        chain,
        documents=parsed,
        filings=filings,
        event_type=EventType.FACILITY_INVESTMENT,
    ).history

    assert history is not None

    root_event = adapted[filings[0].doc_id].event

    state = resolve_latest_event_state(
        root_event=root_event,
        history=history,
        chain=chain,
        documents=adapted,
    )

    expected = {
        "investment_amount": (
            2009800000000,
            "KRW",
            "exchange_20241218800350",
        ),
        "investment_end_date": (
            "2025-04-01",
            None,
            "exchange_20230605800274",
        ),
        "investment_target": (
            "송도 제2바이오캠퍼스 내 5공장 신설",
            None,
            "exchange_20230317800146",
        ),
        "investment_purpose": (
            "바이오의약품 생산 시설 확대를 통한 경쟁력 강화",
            None,
            "exchange_20230317800146",
        ),
        "investment_start_date": (
            "2023-03-17",
            None,
            "exchange_20230317800146",
        ),
        "board_resolution_date": (
            "2023-03-17",
            None,
            "exchange_20230317800146",
        ),
    }

    assert state.as_of.isoformat() == "2024-12-18"
    assert state.event_id == history.event_id
    assert state.event_type is EventType.FACILITY_INVESTMENT
    assert state.company == SAMSUNG_COMPANY

    assert {
        field_name: (
            field.value,
            field.unit,
            field.source_doc_id,
        )
        for field_name, field in state.fields.items()
        if field_name in expected
    } == expected

    assert state.fields["equity_ratio"].value == 22.01
    assert (
        state.fields["equity_ratio"].source_doc_id
        == "exchange_20230317800146"
    )

    for field_name, (_, _, source_doc_id) in expected.items():
        assert state.fields[field_name].evidence_ids == [
            f"evidence_{source_doc_id}_{field_name}"
        ]