from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from disclosure_agent.agent import facility_investment_aggregation as facility
from disclosure_agent.agent.facility_investment_aggregation import (
    REASON_EXTRACTION_FAILED,
    REASON_INCOMPATIBLE_UNIT,
    REASON_INCOMPLETE_CHAIN,
    REASON_MISSING_PROVENANCE,
    REASON_PARSE_FAILED,
    STAGE_EXTRACTION,
    STAGE_PARSING,
    FacilityInvestmentCollection,
    FacilityInvestmentFailure,
    collect_facility_investment_facts,
)
from disclosure_agent.agent.handlers import cross_entity_compare
from disclosure_agent.agent.query_analyzer import build_query_plan
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ComparisonRelation,
    EventType,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
    ResultInputType,
)
from disclosure_agent.common.schemas import (
    Event,
    EventHistory,
    Evidence,
    Fact,
    ResolvedEventState,
    ResolvedFieldValue,
)
from disclosure_agent.event.adapter import EventAdapterResult
from disclosure_agent.event.exceptions import UnsupportedCompositeCorrectionError
from disclosure_agent.event.linker import ChainEntry, FilingChain, LinkResult
from disclosure_agent.retrieval.metadata_filter import FilingRef

LEFT = "테스트전자"
RIGHT = "테스트로보틱스"
Q12 = (
    f"2024년에 {LEFT}하고 {RIGHT}가 새로 발표한 시설투자, "
    "회사별로 다 합치면 어디가 더 커?"
)


def _filing(
    receipt_no: str,
    receipt_date: str,
    *,
    company: str = LEFT,
    correction: bool = False,
) -> FilingRef:
    return FilingRef(
        doc_id=f"exchange_{receipt_no}",
        corp_name=company,
        corp_code=None,
        stock_code=None,
        report_name="정정" if correction else "신규시설투자등",
        receipt_no=receipt_no,
        receipt_date=receipt_date,
        document_class="event",
        doc_group="exchange",
        document_subtype="신규시설투자등",
        base_year=None,
        base_month=None,
        is_correction=correction,
        directory=Path("/synthetic") / receipt_no,
        main_document=Path("/synthetic") / f"{receipt_no}.xml",
    )


def _evidence(doc_id: str) -> Evidence:
    return Evidence(
        evidence_id=f"evidence_{doc_id}_investment_amount",
        doc_id=doc_id,
        location_type=EvidenceLocationType.EVENT_FIELD,
        field_name="investment_amount",
        raw_text="120",
    )


def _adapted(doc_id: str, company: str = LEFT) -> EventAdapterResult:
    evidence = _evidence(doc_id)
    return EventAdapterResult(
        event=Event(
            event_id=f"event_{doc_id}",
            event_type=EventType.FACILITY_INVESTMENT,
            company=company,
            source_doc_id=doc_id,
            attributes={"investment_amount": "120", "investment_amount_unit": "KRW"},
        ),
        evidence=[evidence],
        field_changes=[],
    )


def _state(
    root_doc_id: str,
    *,
    value: str,
    source_doc_id: str,
    unit: str = "KRW",
    evidence_ids: list[str] | None = None,
) -> ResolvedEventState:
    return ResolvedEventState(
        event_id=f"event_facility_investment_{root_doc_id}",
        event_type=EventType.FACILITY_INVESTMENT,
        company=LEFT,
        as_of=date(2026, 1, 1),
        fields={
            "investment_amount": ResolvedFieldValue(
                value=value,
                unit=unit,
                source_doc_id=source_doc_id,
                evidence_ids=(
                    evidence_ids
                    if evidence_ids is not None
                    else [f"evidence_{source_doc_id}_investment_amount"]
                ),
            )
        },
    )


@pytest.fixture
def synthetic_event_stack(monkeypatch: pytest.MonkeyPatch):
    root_a = _filing("ROOT_A", "20240110")
    root_b = _filing("ROOT_B", "20241220")
    root_2025 = _filing("ROOT_C", "20250110")
    amount_correction = _filing("CORR_A", "20250201", correction=True)
    end_date_correction = _filing("CORR_END", "20260101", correction=True)
    unrelated_correction = _filing("CORR_C", "20250210", correction=True)
    candidates = (
        root_a,
        root_b,
        root_2025,
        amount_correction,
        end_date_correction,
        unrelated_correction,
    )
    retrieval_calls: list[dict] = []
    parsed_receipts: list[str] = []
    extracted_receipts: list[str] = []
    adapted_doc_ids: list[str] = []
    linker_candidate_receipts: list[tuple[str, ...]] = []
    chain_entries_by_root: dict[str, tuple[str, ...]] = {}
    parse_failures: set[str] = set()
    extraction_failures: set[str] = set()
    states = {
        "ROOT_A": _state(
            root_a.doc_id,
            value="120",
            source_doc_id=amount_correction.doc_id,
        ),
        "ROOT_B": _state(root_b.doc_id, value="200", source_doc_id=root_b.doc_id),
    }
    incomplete_roots: set[str] = set()

    def fake_list_event_filings(**kwargs):
        retrieval_calls.append(kwargs)
        if kwargs["include_corrections"] is False:
            assert kwargs["year"] == 2024
            return [root_a, root_b]
        assert "year" not in kwargs
        return list(candidates)

    def fake_parse(path: Path):
        parsed_receipts.append(path.stem)
        if path.stem in parse_failures:
            raise ValueError("synthetic malformed document")
        return {"receipt_no": path.stem}

    def fake_extract(document: dict):
        receipt_no = document["receipt_no"]
        extracted_receipts.append(receipt_no)
        if receipt_no in extraction_failures:
            raise ValueError("synthetic extraction failure")
        filing = next(item for item in candidates if item.receipt_no == receipt_no)
        return {
            "doc_id": filing.doc_id,
            "company": filing.corp_name,
            "event_type": EventType.FACILITY_INVESTMENT.value,
            "fields": {
                "investment_amount": {
                    "raw_value": states.get(receipt_no, states["ROOT_A"])
                    .fields["investment_amount"]
                    .value,
                    "raw_unit": "원",
                }
            },
        }

    def fake_adapt(extracted: dict):
        adapted_doc_ids.append(extracted["doc_id"])
        return _adapted(extracted["doc_id"])

    def fake_chain(*, root_receipt_no: str, filings, **kwargs):
        linker_candidate_receipts.append(tuple(item.receipt_no for item in filings))
        root = next(item for item in filings if item.receipt_no == root_receipt_no)
        entries = [ChainEntry(filing=root, is_correction=False)]
        if root_receipt_no == "ROOT_A":
            entries.extend(
                (
                    ChainEntry(filing=amount_correction, is_correction=True),
                    ChainEntry(filing=end_date_correction, is_correction=True),
                )
            )
        unresolved = ()
        if root_receipt_no in incomplete_roots:
            unresolved = (
                LinkResult(
                    status="incomplete_chain",
                    correction_receipt_no="UNRESOLVED",
                    original_filing_date="20240110",
                ),
            )
        chain_entries_by_root[root_receipt_no] = tuple(
            entry.filing.receipt_no for entry in entries
        )
        return FilingChain(
            root_receipt_no=root_receipt_no,
            entries=tuple(entries),
            unresolved_links=unresolved,
        )

    def fake_history(chain: FilingChain, **kwargs):
        return SimpleNamespace(
            history=EventHistory(
                event_id=f"event_facility_investment_exchange_{chain.root_receipt_no}",
                document_ids=[f"exchange_{chain.root_receipt_no}"],
                chain_complete=chain.is_complete,
            )
            if chain.is_complete
            else None
        )

    def fake_resolve(*, chain: FilingChain, **kwargs):
        return states[chain.root_receipt_no]

    monkeypatch.setattr(facility, "list_event_filings", fake_list_event_filings)
    monkeypatch.setattr(facility, "parse_exchange_document", fake_parse)
    monkeypatch.setattr(facility, "extract_event", fake_extract)
    monkeypatch.setattr(facility, "adapt_extracted_event", fake_adapt)
    monkeypatch.setattr(facility, "build_filing_chain", fake_chain)
    monkeypatch.setattr(facility, "to_event_history", fake_history)
    monkeypatch.setattr(facility, "resolve_latest_event_state", fake_resolve)

    return SimpleNamespace(
        states=states,
        incomplete_roots=incomplete_roots,
        retrieval_calls=retrieval_calls,
        parsed_receipts=parsed_receipts,
        extracted_receipts=extracted_receipts,
        adapted_doc_ids=adapted_doc_ids,
        linker_candidate_receipts=linker_candidate_receipts,
        chain_entries_by_root=chain_entries_by_root,
        parse_failures=parse_failures,
        extraction_failures=extraction_failures,
        root_a=root_a,
        root_b=root_b,
        amount_correction=amount_correction,
        unrelated_correction=unrelated_correction,
    )


def test_target_year_roots_are_independently_resolved_without_double_counting(
    synthetic_event_stack,
) -> None:
    result = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert result.root_doc_ids == ("exchange_ROOT_A", "exchange_ROOT_B")
    assert [fact.normalized_value for fact in result.facts] == ["120", "200"]
    assert "exchange_ROOT_C" not in result.root_doc_ids
    assert len(result.facts) == 2
    assert result.failures == ()

    corrected = result.facts[0]
    assert corrected.fact_id == (
        "fact_event_facility_investment_exchange_ROOT_A_investment_amount"
    )
    assert corrected.evidence_ids == [
        "evidence_exchange_CORR_A_investment_amount"
    ]
    assert result.evidence[0].doc_id == "exchange_CORR_A"


def test_source_filing_points_at_the_document_that_actually_supplied_the_latest_amount(
    synthetic_event_stack,
) -> None:
    """
    ROOT_A는 정정(CORR_A)이 금액을 갱신했고 ROOT_B는 원공시 값이 그대로 최신이다.
    source_filings는 root의 FilingRef가 아니라 amount.source_doc_id가 실제로 가리키는
    문서의 FilingRef여야 한다 — 두 root 모두 원공시가 아닌 실제 출처를 따라간다.
    """
    result = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert [filing.doc_id for filing in result.source_filings] == [
        "exchange_CORR_A",
        "exchange_ROOT_B",
    ]

    # Evidence.doc_id와 source_filings.doc_id가 정확히 join 가능해야 한다.
    assert {item.doc_id for item in result.evidence} == {
        filing.doc_id for filing in result.source_filings
    }


@pytest.mark.parametrize("failure_kind", ["parse", "extraction"])
def test_unrelated_later_root_failure_cannot_block_target_year_roots(
    synthetic_event_stack,
    failure_kind: str,
) -> None:
    if failure_kind == "parse":
        synthetic_event_stack.parse_failures.add("ROOT_C")
    else:
        synthetic_event_stack.extraction_failures.add("ROOT_C")

    result = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert result.root_doc_ids == ("exchange_ROOT_A", "exchange_ROOT_B")
    assert [fact.normalized_value for fact in result.facts] == ["120", "200"]
    assert result.failures == ()
    assert "ROOT_C" not in synthetic_event_stack.parsed_receipts
    assert "ROOT_C" not in synthetic_event_stack.extracted_receipts

    # 본문 처리에서만 빼고 linker의 original disambiguation 후보에서는 제거하지 않는다.
    assert synthetic_event_stack.linker_candidate_receipts
    assert all(
        "ROOT_C" in candidates
        for candidates in synthetic_event_stack.linker_candidate_receipts
    )

    # 연도 제한 없는 correction은 여전히 linking/latest-state 후보로 처리한다.
    assert {"CORR_A", "CORR_END"} <= set(synthetic_event_stack.parsed_receipts)
    assert {"CORR_A", "CORR_END"} <= set(
        synthetic_event_stack.extracted_receipts
    )


def test_unrelated_correction_is_parsed_for_linking_but_not_extracted_or_adapted(
    synthetic_event_stack,
) -> None:
    synthetic_event_stack.extraction_failures.add("CORR_C")

    result = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert result.root_doc_ids == ("exchange_ROOT_A", "exchange_ROOT_B")
    assert [fact.normalized_value for fact in result.facts] == ["120", "200"]
    assert result.failures == ()

    assert "CORR_C" in synthetic_event_stack.parsed_receipts
    assert all(
        {"ROOT_C", "CORR_C"} <= set(candidates)
        for candidates in synthetic_event_stack.linker_candidate_receipts
    )
    assert "CORR_C" not in synthetic_event_stack.chain_entries_by_root["ROOT_A"]
    assert "CORR_C" not in synthetic_event_stack.chain_entries_by_root["ROOT_B"]
    assert "CORR_C" not in synthetic_event_stack.extracted_receipts
    assert "exchange_CORR_C" not in synthetic_event_stack.adapted_doc_ids

    assert {"CORR_A", "CORR_END"} <= set(synthetic_event_stack.parsed_receipts)
    assert {"CORR_A", "CORR_END"} <= set(
        synthetic_event_stack.extracted_receipts
    )
    assert {"exchange_CORR_A", "exchange_CORR_END"} <= set(
        synthetic_event_stack.adapted_doc_ids
    )


@pytest.mark.parametrize(
    ("failure_kind", "stage", "reason"),
    [
        ("parse", STAGE_PARSING, REASON_PARSE_FAILED),
        ("extraction", STAGE_EXTRACTION, REASON_EXTRACTION_FAILED),
    ],
)
def test_target_root_failure_preserves_other_root_fact_evidence_at_agent_boundary(
    synthetic_event_stack,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    stage: str,
    reason: str,
) -> None:
    if failure_kind == "parse":
        synthetic_event_stack.parse_failures.add("ROOT_B")
    else:
        synthetic_event_stack.extraction_failures.add("ROOT_B")

    left = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert [fact.normalized_value for fact in left.facts] == ["120"]
    assert left.facts[0].evidence_ids == [
        "evidence_exchange_CORR_A_investment_amount"
    ]
    assert {item.evidence_id for item in left.evidence} == set(
        left.facts[0].evidence_ids
    )
    assert left.evidence[0].doc_id == "exchange_CORR_A"
    assert len(left.failures) == 1
    assert left.failures[0].root_doc_id == "exchange_ROOT_B"
    assert left.failures[0].stage == stage
    assert left.failures[0].reason == reason
    assert all("ROOT_B" not in fact.fact_id for fact in left.facts)

    right = _collection(RIGHT, (_fact(RIGHT, "resolved", "50"),))
    by_company = {LEFT: left, RIGHT: right}
    monkeypatch.setattr(
        cross_entity_compare,
        "collect_facility_investment_facts",
        lambda **kwargs: by_company[kwargs["company"]],
    )
    plan = build_query_plan(Q12, known_companies=(LEFT, RIGHT))

    result = cross_entity_compare.handle_cross_entity_compare(
        plan,
        corpus_root="/synthetic",
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.execution_issue is not None
    assert result.execution_issue.stage == stage
    assert result.execution_issue.reason == reason
    assert left.facts[0] in result.facts
    assert left.evidence[0] in result.evidence
    assert result.calculations == []
    assert result.comparisons == []


def test_correction_parse_failure_remains_collection_level_fail_closed(
    synthetic_event_stack,
) -> None:
    synthetic_event_stack.parse_failures.add("CORR_C")

    result = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert result.facts == ()
    assert result.evidence == ()
    assert result.failures[-1].root_doc_id == ""
    assert result.failures[-1].stage == STAGE_PARSING
    assert result.failures[-1].reason == REASON_PARSE_FAILED
    assert "exchange_CORR_C" in result.failures[-1].detail
    assert "CORR_C" in synthetic_event_stack.parsed_receipts
    assert synthetic_event_stack.linker_candidate_receipts == []


def test_later_other_field_correction_keeps_prior_amount_provenance(
    synthetic_event_stack,
) -> None:
    result = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert result.facts[0].normalized_value == "120"
    assert result.facts[0].evidence_ids == [
        "evidence_exchange_CORR_A_investment_amount"
    ]


def test_incomplete_root_is_excluded_without_original_amount_fallback(
    synthetic_event_stack,
) -> None:
    synthetic_event_stack.incomplete_roots.add("ROOT_B")

    result = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert [fact.normalized_value for fact in result.facts] == ["120"]
    assert [failure.root_doc_id for failure in result.failures] == ["exchange_ROOT_B"]
    assert result.failures[0].reason == REASON_INCOMPLETE_CHAIN


@pytest.mark.parametrize(
    ("state_override", "reason"),
    [
        ({"unit": "USD"}, REASON_INCOMPATIBLE_UNIT),
        ({"evidence_ids": []}, REASON_MISSING_PROVENANCE),
    ],
)
def test_unusable_latest_amount_is_not_promoted_to_fact(
    synthetic_event_stack,
    state_override: dict,
    reason: str,
) -> None:
    synthetic_event_stack.states["ROOT_B"] = _state(
        "exchange_ROOT_B",
        value="200",
        source_doc_id="exchange_ROOT_B",
        **state_override,
    )

    result = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert [fact.normalized_value for fact in result.facts] == ["120"]
    assert result.failures[0].reason == reason


def test_unsupported_composite_correction_is_distinct_from_integrity_value_error(
    synthetic_event_stack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unsupported(*, chain: FilingChain, **kwargs):
        if chain.root_receipt_no == "ROOT_B":
            raise UnsupportedCompositeCorrectionError(
                source_doc_id="exchange_CORR_COMPOSITE",
                category="source_composite",
            )
        return synthetic_event_stack.states[chain.root_receipt_no]

    monkeypatch.setattr(facility, "resolve_latest_event_state", unsupported)
    result = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert result.failures[0].reason == "unsupported_composite_correction"

    def integrity_failure(**kwargs):
        raise ValueError("history identity mismatch")

    monkeypatch.setattr(facility, "resolve_latest_event_state", integrity_failure)
    result = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root="/synthetic",
    )

    assert {failure.reason for failure in result.failures} == {
        "latest_state_resolution_failed"
    }


def _fact(company: str, suffix: str, value: str) -> Fact:
    evidence_id = f"evidence_{company}_{suffix}"
    return Fact(
        fact_id=f"fact_{company}_{suffix}",
        company=company,
        metric=MetricConcept.INVESTMENT_AMOUNT,
        accounting_scope=FactAccountingScope.NOT_APPLICABLE,
        normalized_value=value,
        normalized_unit="KRW",
        evidence_ids=[evidence_id],
    )


def _collection(company: str, facts: tuple[Fact, ...]) -> FacilityInvestmentCollection:
    evidence = tuple(
        Evidence(
            evidence_id=fact.evidence_ids[0],
            doc_id=f"doc_{fact.fact_id}",
            location_type=EvidenceLocationType.EVENT_FIELD,
            field_name="investment_amount",
        )
        for fact in facts
    )
    return FacilityInvestmentCollection(
        company=company,
        root_doc_ids=tuple(f"root_{fact.fact_id}" for fact in facts),
        facts=facts,
        evidence=evidence,
    )


def test_zero_amount_cancellation_contributes_zero_with_correction_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """같은 subtype 정정이 투자금액을 0으로 명시하는 취소 표현을 고정한다."""

    def materialize(filing: FilingRef, body: str) -> FilingRef:
        directory = tmp_path / filing.receipt_no
        directory.mkdir()
        main_document = directory / f"{filing.receipt_no}.xml"
        main_document.write_text(
            f"<html><head><title>신규시설투자등</title></head><body>{body}</body></html>",
            encoding="utf-8",
        )
        return replace(
            filing,
            directory=directory,
            main_document=main_document,
        )

    cancelled_root = materialize(
        _filing("CANCELLED_ROOT", "20240110"),
        """
        <span>신규 시설투자 등</span>
        <table id="XFormD1_Form0_Table0">
          <tr><td>2. 투자내역</td><td>투자금액(원)</td><td>100</td></tr>
        </table>
        """,
    )
    live_root = materialize(
        _filing("LIVE_ROOT", "20240210"),
        """
        <span>신규 시설투자 등</span>
        <table id="XFormD1_Form0_Table0">
          <tr><td>2. 투자내역</td><td>투자금액(원)</td><td>50</td></tr>
        </table>
        """,
    )
    cancellation = materialize(
        _filing("CANCEL", "20250110", correction=True),
        """
        <span>정정신고(보고)</span><span>신규 시설투자 등</span>
        <table id="XFormD8_Form0_Table1">
          <tr><td>정정일자</td><td>2025-01-10</td></tr>
          <tr><td>1. 정정관련 공시서류</td><td>신규시설투자등</td></tr>
          <tr><td>2. 정정관련 공시서류제출일</td><td>2024-01-10</td></tr>
          <tr><td>3. 정정사유</td><td>시설투자 취소</td></tr>
        </table>
        <table id="XFormD8_Form0_RepeatTable0">
          <tr><td>정정항목</td><td>정정전</td><td>정정후</td></tr>
          <tr><td>2. 투자내역 - 투자금액(원)</td><td>100</td><td>0</td></tr>
        </table>
        <table id="XFormD1_Form0_Table0">
          <tr><td>2. 투자내역</td><td>투자금액(원)</td><td>0</td></tr>
        </table>
        """,
    )
    candidates = [cancelled_root, live_root, cancellation]
    chains_by_root: dict[str, FilingChain] = {}
    states_by_root: dict[str, ResolvedEventState] = {}
    real_build_filing_chain = facility.build_filing_chain
    real_resolve_latest_event_state = facility.resolve_latest_event_state

    def fake_list_event_filings(**kwargs):
        if kwargs["include_corrections"] is False:
            return [cancelled_root, live_root]
        return candidates

    def capture_chain(*, root_receipt_no: str, **kwargs) -> FilingChain:
        chain = real_build_filing_chain(
            root_receipt_no=root_receipt_no,
            **kwargs,
        )
        chains_by_root[root_receipt_no] = chain
        return chain

    def capture_state(*, chain: FilingChain, **kwargs) -> ResolvedEventState:
        state = real_resolve_latest_event_state(chain=chain, **kwargs)
        states_by_root[chain.root_receipt_no] = state
        return state

    monkeypatch.setattr(facility, "list_event_filings", fake_list_event_filings)
    monkeypatch.setattr(facility, "build_filing_chain", capture_chain)
    monkeypatch.setattr(facility, "resolve_latest_event_state", capture_state)

    collection = collect_facility_investment_facts(
        company=LEFT,
        target_year=2024,
        corpus_root=tmp_path,
    )

    assert collection.root_doc_ids == (
        "exchange_CANCELLED_ROOT",
        "exchange_LIVE_ROOT",
    )
    assert collection.failures == ()
    assert [fact.normalized_value for fact in collection.facts] == ["0", "50"]

    cancellation_chain = chains_by_root["CANCELLED_ROOT"]
    assert [entry.filing.receipt_no for entry in cancellation_chain.entries] == [
        "CANCELLED_ROOT",
        "CANCEL",
    ]
    assert [
        (change.field, change.before, change.after)
        for change in cancellation_chain.entries[1].changes
    ] == [("investment_amount", "100", "0")]

    cancelled_amount = states_by_root["CANCELLED_ROOT"].fields[
        "investment_amount"
    ]
    assert Decimal(str(cancelled_amount.value)) == Decimal("0")
    assert cancelled_amount.source_doc_id == "exchange_CANCEL"
    assert cancelled_amount.evidence_ids == [
        "evidence_exchange_CANCEL_investment_amount"
    ]

    cancelled_fact = collection.facts[0]
    cancellation_evidence_id = "evidence_exchange_CANCEL_investment_amount"

    assert cancelled_fact.raw_value == "0"
    assert cancelled_fact.evidence_ids == [cancellation_evidence_id]
    assert any(
        item.evidence_id == cancellation_evidence_id
        and item.doc_id == "exchange_CANCEL"
        and item.raw_text == "0"
        for item in collection.evidence
    )

    right = _collection(RIGHT, (_fact(RIGHT, "resolved", "40"),))
    by_company = {LEFT: collection, RIGHT: right}
    monkeypatch.setattr(
        cross_entity_compare,
        "collect_facility_investment_facts",
        lambda **kwargs: by_company[kwargs["company"]],
    )
    plan = build_query_plan(Q12, known_companies=(LEFT, RIGHT))

    result = cross_entity_compare.handle_cross_entity_compare(
        plan,
        corpus_root="/synthetic",
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert [item.value for item in result.calculations] == ["50", "40"]
    assert [len(item.inputs) for item in result.calculations] == [2, 1]
    assert len(result.comparisons) == 1
    assert result.comparisons[0].relation is ComparisonRelation.LEFT_GREATER


def test_company_aggregates_and_comparison_preserve_question_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    left_collection = _collection(
        LEFT,
        (_fact(LEFT, "a", "100"), _fact(LEFT, "b", "200")),
    )
    right_collection = _collection(RIGHT, (_fact(RIGHT, "a", "50"),))
    by_company = {LEFT: left_collection, RIGHT: right_collection}
    monkeypatch.setattr(
        cross_entity_compare,
        "collect_facility_investment_facts",
        lambda **kwargs: by_company[kwargs["company"]],
    )
    plan = build_query_plan(Q12, known_companies=(LEFT, RIGHT))

    result = cross_entity_compare.handle_cross_entity_compare(
        plan,
        corpus_root="/synthetic",
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert [item.value for item in result.calculations] == ["300", "50"]
    assert [len(item.inputs) for item in result.calculations] == [2, 1]
    assert all(
        reference.input_type is ResultInputType.FACT
        for calculation in result.calculations
        for reference in calculation.inputs
    )
    assert len(result.comparisons) == 1
    comparison = result.comparisons[0]
    assert comparison.relation is ComparisonRelation.LEFT_GREATER
    assert comparison.left.input_id == result.calculations[0].calculation_id
    assert comparison.right.input_id == result.calculations[1].calculation_id


def test_result_carries_a_source_document_for_each_companys_actual_filing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    entity_filings -> source_documents 배선이 실제 doc_id로 join되고, 회사가 둘이어도
    문서가 중복 없이 각각 한 건씩 실린다(Evidence.doc_id와 join 가능해야 한다).
    """
    left_filing = _filing("ROOT_LEFT", "20240110", company=LEFT)
    right_filing = _filing("ROOT_RIGHT", "20240115", company=RIGHT)

    left_fact = _fact(LEFT, "resolved", "100")
    right_fact = _fact(RIGHT, "resolved", "50")

    left_collection = FacilityInvestmentCollection(
        company=LEFT,
        root_doc_ids=(left_filing.doc_id,),
        facts=(left_fact,),
        evidence=(
            Evidence(
                evidence_id=left_fact.evidence_ids[0],
                doc_id=left_filing.doc_id,
                location_type=EvidenceLocationType.EVENT_FIELD,
                field_name="investment_amount",
            ),
        ),
        source_filings=(left_filing,),
    )
    right_collection = FacilityInvestmentCollection(
        company=RIGHT,
        root_doc_ids=(right_filing.doc_id,),
        facts=(right_fact,),
        evidence=(
            Evidence(
                evidence_id=right_fact.evidence_ids[0],
                doc_id=right_filing.doc_id,
                location_type=EvidenceLocationType.EVENT_FIELD,
                field_name="investment_amount",
            ),
        ),
        source_filings=(right_filing,),
    )
    by_company = {LEFT: left_collection, RIGHT: right_collection}
    monkeypatch.setattr(
        cross_entity_compare,
        "collect_facility_investment_facts",
        lambda **kwargs: by_company[kwargs["company"]],
    )
    plan = build_query_plan(Q12, known_companies=(LEFT, RIGHT))

    result = cross_entity_compare.handle_cross_entity_compare(
        plan,
        corpus_root="/synthetic",
    )

    known_doc_ids = {doc.doc_id for doc in result.source_documents}

    assert known_doc_ids == {left_filing.doc_id, right_filing.doc_id}
    assert all(item.doc_id in known_doc_ids for item in result.evidence)

    by_doc_id = {doc.doc_id: doc for doc in result.source_documents}
    assert by_doc_id[left_filing.doc_id].company == LEFT
    assert by_doc_id[right_filing.doc_id].company == RIGHT


def test_blocked_facility_aggregate_has_no_source_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    실제 선택 FilingRef를 확보하지 못한 결과에는 문서 metadata를 지어내지 않는다.

    _collection 헬퍼는 source_filings를 채우지 않으므로, 여기서 source_documents가 비는 것은
    PARTIALLY_ANSWERABLE이라는 status 때문이 아니라 실제 FilingRef가 없기 때문이다 — 아래
    test_partially_answerable_aggregate_still_carries_source_documents가 같은 갈래에서
    FilingRef가 있으면 채워진다는 것을 대조로 보여준다.
    """
    left_fact = _fact(LEFT, "resolved", "100")
    left = _collection(LEFT, (left_fact,))
    left = FacilityInvestmentCollection(
        company=left.company,
        root_doc_ids=(*left.root_doc_ids, "root_unresolved"),
        facts=left.facts,
        evidence=left.evidence,
        failures=(
            FacilityInvestmentFailure(
                company=LEFT,
                root_doc_id="root_unresolved",
                stage="latest_state_resolution",
                reason=REASON_INCOMPLETE_CHAIN,
                detail="chain incomplete",
            ),
        ),
    )
    right = _collection(RIGHT, (_fact(RIGHT, "resolved", "50"),))
    by_company = {LEFT: left, RIGHT: right}
    monkeypatch.setattr(
        cross_entity_compare,
        "collect_facility_investment_facts",
        lambda **kwargs: by_company[kwargs["company"]],
    )
    plan = build_query_plan(Q12, known_companies=(LEFT, RIGHT))

    result = cross_entity_compare.handle_cross_entity_compare(
        plan,
        corpus_root="/synthetic",
    )

    assert result.answerability_status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert result.source_documents == []


def _collection_with_source_filing(
    company: str,
    fact: Fact,
    filing: FilingRef,
) -> FacilityInvestmentCollection:
    """해소에 성공한 root 하나를, 그 값이 실제로 나온 문서까지 함께 담은 collection."""
    return FacilityInvestmentCollection(
        company=company,
        root_doc_ids=(filing.doc_id,),
        facts=(fact,),
        evidence=(
            Evidence(
                evidence_id=fact.evidence_ids[0],
                doc_id=filing.doc_id,
                location_type=EvidenceLocationType.EVENT_FIELD,
                field_name="investment_amount",
            ),
        ),
        source_filings=(filing,),
    )


def test_partially_answerable_aggregate_still_carries_source_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    한 root의 chain을 완결하지 못해 PARTIALLY_ANSWERABLE이어도, 해소에 성공한 root의 문서는
    실제 근거이므로 그대로 싣는다.

    status가 ANSWERABLE이 아니라는 사실만으로 문서 metadata를 지우지 않는다. 반대로 실패해
    집계에서 빠진 root의 문서는 Evidence에도 없으므로 새지 않는다.
    """
    left_fact = _fact(LEFT, "resolved", "100")
    left_filing = _filing("ROOT_LEFT", "20240110", company=LEFT)
    left = _collection_with_source_filing(LEFT, left_fact, left_filing)
    left = FacilityInvestmentCollection(
        company=left.company,
        root_doc_ids=(*left.root_doc_ids, "exchange_ROOT_UNRESOLVED"),
        facts=left.facts,
        evidence=left.evidence,
        source_filings=left.source_filings,
        failures=(
            FacilityInvestmentFailure(
                company=LEFT,
                root_doc_id="exchange_ROOT_UNRESOLVED",
                stage="latest_state_resolution",
                reason=REASON_INCOMPLETE_CHAIN,
                detail="chain incomplete",
            ),
        ),
    )

    right_fact = _fact(RIGHT, "resolved", "50")
    right_filing = _filing("ROOT_RIGHT", "20240115", company=RIGHT)
    right = _collection_with_source_filing(RIGHT, right_fact, right_filing)

    by_company = {LEFT: left, RIGHT: right}
    monkeypatch.setattr(
        cross_entity_compare,
        "collect_facility_investment_facts",
        lambda **kwargs: by_company[kwargs["company"]],
    )
    plan = build_query_plan(Q12, known_companies=(LEFT, RIGHT))

    result = cross_entity_compare.handle_cross_entity_compare(
        plan,
        corpus_root="/synthetic",
    )

    # 기존 판정과 값은 그대로다.
    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.INCOMPLETE_CHAIN
    assert {fact.fact_id for fact in result.facts} == {
        left_fact.fact_id,
        right_fact.fact_id,
    }
    assert result.calculations == []
    assert result.comparisons == []

    evidence_doc_ids = {item.doc_id for item in result.evidence}
    source_document_ids = {doc.doc_id for doc in result.source_documents}
    assert evidence_doc_ids == source_document_ids == {
        left_filing.doc_id,
        right_filing.doc_id,
    }

    # 해소하지 못해 집계에서 빠진 root는 문서로 새지 않는다.
    assert "exchange_ROOT_UNRESOLVED" not in source_document_ids

    by_doc_id = {doc.doc_id: doc for doc in result.source_documents}
    assert by_doc_id[left_filing.doc_id].company == LEFT
    assert by_doc_id[left_filing.doc_id].report_name == left_filing.report_name
    assert by_doc_id[left_filing.doc_id].receipt_no == left_filing.receipt_no
    assert by_doc_id[left_filing.doc_id].filing_date == date(2024, 1, 10)


def test_partial_chain_preserves_supported_facts_and_structured_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    left_fact = _fact(LEFT, "resolved", "100")
    left = _collection(LEFT, (left_fact,))
    left = FacilityInvestmentCollection(
        company=left.company,
        root_doc_ids=(*left.root_doc_ids, "root_unresolved"),
        facts=left.facts,
        evidence=left.evidence,
        failures=(
            FacilityInvestmentFailure(
                company=LEFT,
                root_doc_id="root_unresolved",
                stage="latest_state_resolution",
                reason=REASON_INCOMPLETE_CHAIN,
                detail="chain incomplete",
            ),
        ),
    )
    right = _collection(RIGHT, (_fact(RIGHT, "resolved", "50"),))
    by_company = {LEFT: left, RIGHT: right}
    monkeypatch.setattr(
        cross_entity_compare,
        "collect_facility_investment_facts",
        lambda **kwargs: by_company[kwargs["company"]],
    )
    plan = build_query_plan(Q12, known_companies=(LEFT, RIGHT))

    result = cross_entity_compare.handle_cross_entity_compare(
        plan,
        corpus_root="/synthetic",
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.INCOMPLETE_CHAIN
    assert {fact.fact_id for fact in result.facts} == {
        left_fact.fact_id,
        right.facts[0].fact_id,
    }
    assert result.calculations == []
    assert result.comparisons == []
    assert result.conversion_gaps[0].dropped["root_doc_id"] == "root_unresolved"


@pytest.mark.parametrize(
    "failure_reason",
    ["unsupported_composite_correction", "latest_state_resolution_failed"],
)
def test_agent_boundary_keeps_composite_policy_distinct_from_integrity_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_reason: str,
) -> None:
    left = FacilityInvestmentCollection(
        company=LEFT,
        root_doc_ids=("root_failed",),
        failures=(
            FacilityInvestmentFailure(
                company=LEFT,
                root_doc_id="root_failed",
                stage="latest_state_resolution",
                reason=failure_reason,
                detail="resolution failed",
            ),
        ),
    )
    right = _collection(RIGHT, (_fact(RIGHT, "resolved", "50"),))
    by_company = {LEFT: left, RIGHT: right}
    monkeypatch.setattr(
        cross_entity_compare,
        "collect_facility_investment_facts",
        lambda **kwargs: by_company[kwargs["company"]],
    )
    plan = build_query_plan(Q12, known_companies=(LEFT, RIGHT))

    result = cross_entity_compare.handle_cross_entity_compare(
        plan,
        corpus_root="/synthetic",
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.execution_issue.stage == "latest_state_resolution"
    assert result.execution_issue.reason == failure_reason
    assert result.calculations == []
    assert result.comparisons == []
