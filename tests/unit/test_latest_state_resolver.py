"""Latest-state resolver unit and actual-corpus regressions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from disclosure_agent.common.enums import EventType, EvidenceLocationType
from disclosure_agent.common.schemas import Event, EventHistory, Evidence
from disclosure_agent.event.adapter import EventAdapterResult, adapt_extracted_event
from disclosure_agent.event.contract_adapter import to_event_history
from disclosure_agent.event.diff import FieldChange
from disclosure_agent.event.exceptions import UnsupportedCompositeCorrectionError
from disclosure_agent.event.extractor import extract_event
from disclosure_agent.event.latest_state import resolve_latest_event_state
from disclosure_agent.event.linker import ChainEntry, FilingChain, build_filing_chain
from disclosure_agent.event.parser import parse_exchange_document
from disclosure_agent.retrieval.metadata_filter import list_event_filings

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"
DEFAULT_EXCHANGE_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "exchange"
)
SAMSUNG_COMPANY = "삼성바이오로직스"
SAMSUNG_FILINGS = (
    ("20230317800146", "2023-03-17", False),
    ("20230605800274", "2023-06-05", True),
    ("20241218800350", "2024-12-18", True),
)

SUPPLY_COMPANY = "삼성전자"
SUPPLY_CONTRACT_FILINGS = (
    ("20250728800035", "2025-07-28", False),
    ("20250731800028", "2025-07-31", True),
)

Q06_COMPANY = "LG유플러스"
Q06_DOCUMENT_SUBTYPE = "신규시설투자등"
Q06_SEMANTIC_TOKEN = "AIDC"
Q06_FIELD_NAMES = {
    "investment_type",
    "investment_target",
    "investment_amount",
    "equity",
    "equity_ratio",
    "large_corporation",
    "investment_purpose",
    "investment_start_date",
    "investment_end_date",
    "board_resolution_date",
    "notes",
}


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


def _inputs_with_unmapped_change(
    raw_field: str,
    *,
    event_type: EventType,
):
    root_event, history, chain, documents = _synthetic_inputs()
    entries = tuple(
        ChainEntry(
            filing=entry.filing,
            is_correction=entry.is_correction,
            link=entry.link,
            changes=(FieldChange(None, raw_field, "before", "after"),)
            if entry.filing.receipt_no == "SECOND"
            else entry.changes,
        )
        for entry in chain.entries
        if entry.filing.receipt_no != "FIRST"
    )
    chain = FilingChain(root_receipt_no=chain.root_receipt_no, entries=entries)
    for result in documents.values():
        result.event = result.event.model_copy(update={"event_type": event_type})
    root_event = documents["exchange_ROOT"].event
    history = history.model_copy(
        update={
            "event_id": f"event_{event_type.value}_exchange_ROOT",
            "document_ids": ["exchange_ROOT", "exchange_SECOND"],
        }
    )
    return root_event, history, chain, documents


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


@pytest.mark.parametrize(
    ("raw_field", "category"),
    [
        (
            "2. 계약내역 - 계약금액(원) - 매출액대비(%)",
            "source_composite",
        ),
        ("2.계약내역", "ambiguous_container"),
    ],
)
def test_known_unsafe_supply_correction_blocks_latest_state(
    raw_field: str,
    category: str,
) -> None:
    root_event, history, chain, documents = _inputs_with_unmapped_change(
        raw_field,
        event_type=EventType.SUPPLY_CONTRACT,
    )

    with pytest.raises(
        UnsupportedCompositeCorrectionError,
        match=rf"exchange_SECOND.*{category}",
    ):
        resolve_latest_event_state(
            root_event=root_event,
            history=history,
            chain=chain,
            documents=documents,
        )


def test_source_composite_cannot_confirm_stale_contract_amount_as_latest() -> None:
    root_event, history, chain, documents = _inputs_with_unmapped_change(
        "2. 계약내역 - 계약금액(원) - 매출액대비(%)",
        event_type=EventType.SUPPLY_CONTRACT,
    )
    old_value = "OLD_VALUE"
    root_result = documents["exchange_ROOT"]
    root_result.event = root_result.event.model_copy(
        update={"attributes": {"contract_amount": old_value}}
    )
    root_event = root_result.event

    with pytest.raises(UnsupportedCompositeCorrectionError):
        state = resolve_latest_event_state(
            root_event=root_event,
            history=history,
            chain=chain,
            documents=documents,
        )
        assert state.fields["contract_amount"].value != old_value


def test_facility_composite_cannot_confirm_stale_investment_amount_as_latest() -> None:
    root_event, history, chain, documents = _inputs_with_unmapped_change(
        "투자내역 / 투자금액(원) / 자기자본대비(%)",
        event_type=EventType.FACILITY_INVESTMENT,
    )

    with pytest.raises(UnsupportedCompositeCorrectionError) as exc_info:
        resolve_latest_event_state(
            root_event=root_event,
            history=history,
            chain=chain,
            documents=documents,
        )

    assert exc_info.value.source_doc_id == "exchange_SECOND"
    assert exc_info.value.category == "source_composite"


@pytest.mark.parametrize(
    ("raw_field", "event_type"),
    [
        ("기타 투자판단과 관련한 중요사항", EventType.SUPPLY_CONTRACT),
        ("완전히 모르는 정정항목", EventType.SUPPLY_CONTRACT),
        (
            "2. 계약내역 - 계약금액(원) - 매출액대비(%)",
            EventType.FACILITY_INVESTMENT,
        ),
    ],
)
def test_nonblocking_unmapped_change_keeps_existing_resolution_behavior(
    raw_field: str,
    event_type: EventType,
) -> None:
    root_event, history, chain, documents = _inputs_with_unmapped_change(
        raw_field,
        event_type=event_type,
    )

    state = resolve_latest_event_state(
        root_event=root_event,
        history=history,
        chain=chain,
        documents=documents,
    )

    assert state.fields["investment_amount"].value == 100
    assert state.fields["investment_amount"].source_doc_id == "exchange_ROOT"



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


def _actual_exchange_root(
    filings: tuple[tuple[str, str, bool], ...],
) -> Path | None:
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
            for receipt_no, _, _ in filings
        ):
            return candidate

    return None


def _q06_corpus_paths() -> tuple[Path, Path] | None:
    raw_corpus_root = os.environ.get(CORPUS_ROOT_ENV)

    if not raw_corpus_root:
        return None

    corpus_root = Path(raw_corpus_root).expanduser()
    raw_manifest = os.environ.get(CORPUS_MANIFEST_ENV)
    manifest_path = (
        Path(raw_manifest).expanduser()
        if raw_manifest
        else corpus_root / "manifest.jsonl"
    )

    return corpus_root, manifest_path


@pytest.mark.parametrize(
    ("explicit_manifest", "expected_manifest"),
    [
        ("/explicit/custom.jsonl", Path("/explicit/custom.jsonl")),
        (None, Path("/some/root/manifest.jsonl")),
    ],
)
def test_q06_corpus_paths_respect_explicit_manifest_precedence(
    monkeypatch: pytest.MonkeyPatch,
    explicit_manifest: str | None,
    expected_manifest: Path,
) -> None:
    monkeypatch.setenv(CORPUS_ROOT_ENV, "/some/root")

    if explicit_manifest is None:
        monkeypatch.delenv(CORPUS_MANIFEST_ENV, raising=False)
    else:
        monkeypatch.setenv(CORPUS_MANIFEST_ENV, explicit_manifest)

    assert _q06_corpus_paths() == (Path("/some/root"), expected_manifest)


def test_samsung_biologics_actual_facility_investment_latest_state() -> None:
    corpus_root = _actual_exchange_root(SAMSUNG_FILINGS)

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


@pytest.mark.corpus
def test_q06_actual_facility_investment_latest_state_and_provenance() -> None:
    corpus_paths = _q06_corpus_paths()

    if corpus_paths is None:
        pytest.skip(f"{CORPUS_ROOT_ENV} is not set")

    corpus_root, manifest_path = corpus_paths

    if not manifest_path.is_file():
        pytest.skip(f"corpus manifest does not exist: {manifest_path}")

    candidate_filings = list_event_filings(
        manifest_path=manifest_path,
        corpus_root=corpus_root,
        company=Q06_COMPANY,
        include_corrections=True,
    )
    candidate_documents = {
        filing.receipt_no: parse_exchange_document(
            filing.main_document,
            corp_name=filing.corp_name,
            report_name=filing.report_name,
            receipt_date=filing.receipt_date,
        )
        for filing in candidate_filings
    }
    candidate_events = {
        filing.doc_id: extract_event(candidate_documents[filing.receipt_no])
        for filing in candidate_filings
    }

    matching_roots = []

    for filing in candidate_filings:
        extracted_event = candidate_events[filing.doc_id]
        fields = extracted_event.get("fields") or {}
        semantic_text = " ".join(
            str((fields.get(field_name) or {}).get("normalized_value") or "")
            for field_name in ("investment_target", "investment_purpose")
        )

        if (
            not filing.is_correction
            and extracted_event.get("event_type")
            == EventType.FACILITY_INVESTMENT.value
            and Q06_SEMANTIC_TOKEN in semantic_text
        ):
            matching_roots.append(filing)

    assert len(matching_roots) == 1, (
        "Q06 requires exactly one non-correction AIDC facility-investment "
        f"root, found {len(matching_roots)}"
    )

    root = matching_roots[0]
    root_extracted = candidate_events[root.doc_id]
    root_fields = root_extracted["fields"]

    assert root.doc_id
    assert root.receipt_no
    assert root.receipt_date
    assert root.corp_name == Q06_COMPANY
    assert root.document_subtype == Q06_DOCUMENT_SUBTYPE
    assert root.is_correction is False
    assert root_extracted["company"] == root.corp_name
    assert root_extracted["event_type"] == EventType.FACILITY_INVESTMENT.value
    assert root_extracted["correction"] is None
    assert Q06_SEMANTIC_TOKEN in " ".join(
        str(root_fields[field_name]["normalized_value"])
        for field_name in ("investment_target", "investment_purpose")
    )

    filings = [
        filing
        for filing in candidate_filings
        if filing.document_subtype == root.document_subtype
    ]
    parsed = {
        filing.receipt_no: candidate_documents[filing.receipt_no]
        for filing in filings
    }
    extracted = {
        filing.doc_id: candidate_events[filing.doc_id]
        for filing in filings
    }
    adapted = {
        doc_id: adapt_extracted_event(event)
        for doc_id, event in extracted.items()
    }

    chain = build_filing_chain(
        root_receipt_no=root.receipt_no,
        filings=filings,
        documents=parsed,
        event_type=EventType.FACILITY_INVESTMENT.value,
    )

    assert chain.root_receipt_no == root.receipt_no
    assert len(chain.entries) == 1
    assert chain.entries[0].filing.doc_id == root.doc_id
    assert all(not entry.is_correction for entry in chain.entries)
    assert chain.is_complete is True
    assert chain.unresolved_links == ()

    conversion = to_event_history(
        chain,
        documents=parsed,
        filings=filings,
        event_type=EventType.FACILITY_INVESTMENT,
    )

    assert conversion.history is not None
    assert conversion.links == ()
    assert conversion.conversion_gaps == ()

    history = conversion.history

    assert history.document_ids == [root.doc_id]
    assert history.changes == []
    assert history.chain_complete is True

    state = resolve_latest_event_state(
        root_event=adapted[root.doc_id].event,
        history=history,
        chain=chain,
        documents=adapted,
    )

    assert state.event_id == history.event_id
    assert state.company == root.corp_name
    assert state.event_type is EventType.FACILITY_INVESTMENT
    assert state.as_of.strftime("%Y%m%d") == root.receipt_date
    assert Q06_FIELD_NAMES <= state.fields.keys()

    for field_name in Q06_FIELD_NAMES:
        value = state.fields[field_name].value

        assert value is not None
        assert not isinstance(value, str) or value.strip()

    amount = Decimal(str(state.fields["investment_amount"].value))
    equity = Decimal(str(state.fields["equity"].value))
    equity_ratio = Decimal(str(state.fields["equity_ratio"].value))

    assert amount.is_finite() and amount >= 0
    assert equity.is_finite() and equity >= 0
    assert equity_ratio.is_finite() and 0 <= equity_ratio <= 100
    assert state.fields["investment_amount"].unit == "KRW"
    assert state.fields["equity"].unit == "KRW"
    assert state.fields["equity_ratio"].unit == "%"

    for field_name in (
        "investment_start_date",
        "investment_end_date",
        "board_resolution_date",
    ):
        value = str(state.fields[field_name].value)

        assert date.fromisoformat(value).isoformat() == value

    assert Q06_SEMANTIC_TOKEN in " ".join(
        str(state.fields[field_name].value)
        for field_name in ("investment_target", "investment_purpose")
    )

    for field_name in Q06_FIELD_NAMES:
        state_value = state.fields[field_name].value
        extracted_value = root_fields[field_name]["normalized_value"]

        if field_name in {"investment_amount", "equity", "equity_ratio"}:
            assert Decimal(str(state_value)) == Decimal(str(extracted_value))
        else:
            assert state_value == extracted_value

        assert state.fields[field_name].unit == root_fields[field_name].get(
            "normalized_unit"
        )

    evidence_by_id = {
        item.evidence_id: item
        for result in adapted.values()
        for item in result.evidence
    }
    chain_doc_ids = {entry.filing.doc_id for entry in chain.entries}

    for field_name in Q06_FIELD_NAMES:
        field = state.fields[field_name]

        assert field.source_doc_id in chain_doc_ids
        assert field.evidence_ids

        for evidence_id in field.evidence_ids:
            assert evidence_id in evidence_by_id
            evidence = evidence_by_id[evidence_id]

            assert evidence.doc_id == field.source_doc_id
            assert evidence.field_name == field_name
            assert evidence.raw_text is not None
            assert evidence.raw_text.strip()


def test_samsung_supply_contract_actual_latest_state() -> None:
    corpus_root = _actual_exchange_root(SUPPLY_CONTRACT_FILINGS)

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
            corp_name=SUPPLY_COMPANY,
            document_subtype="단일판매공급계약체결",
            is_correction=is_correction,
        )
        for receipt_no, receipt_date, is_correction in SUPPLY_CONTRACT_FILINGS
    ]

    parsed = {
        filing.receipt_no: parse_exchange_document(
            corpus_root / f"{filing.receipt_no}.xml",
            corp_name=SUPPLY_COMPANY,
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
        root_receipt_no=SUPPLY_CONTRACT_FILINGS[0][0],
        filings=list(reversed(filings)),
        documents=parsed,
        event_type=EventType.SUPPLY_CONTRACT.value,
    )

    history = to_event_history(
        chain,
        documents=parsed,
        filings=filings,
        event_type=EventType.SUPPLY_CONTRACT,
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
        "counterparty": (
            "테슬라(Tesla, Inc.)",
            None,
            "exchange_20250731800028",
        ),
        "contract_amount": (
            22764764160000,
            "KRW",
            "exchange_20250728800035",
        ),
        "contract_name": (
            "반도체 위탁생산 공급계약",
            None,
            "exchange_20250728800035",
        ),
        "contract_start_date": (
            "2025-07-24",
            None,
            "exchange_20250728800035",
        ),
        "contract_end_date": (
            "2033-12-31",
            None,
            "exchange_20250728800035",
        ),
        "contract_date": (
            "2025-07-26",
            None,
            "exchange_20250728800035",
        ),
    }

    assert state.as_of.isoformat() == "2025-07-31"
    assert state.event_id == history.event_id
    assert state.event_type is EventType.SUPPLY_CONTRACT
    assert state.company == SUPPLY_COMPANY

    assert {
        field_name: (
            field.value,
            field.unit,
            field.source_doc_id,
        )
        for field_name, field in state.fields.items()
        if field_name in expected
    } == expected

    # notes는 Extractor에서는 change로 보이지만 EventHistory vocabulary에서는
    # 지원하지 않으므로 latest state에 새 field로 추가하지 않는다.
    assert "notes" not in state.fields

    for field_name, (_, _, source_doc_id) in expected.items():
        assert state.fields[field_name].evidence_ids == [
            f"evidence_{source_doc_id}_{field_name}"
        ]
