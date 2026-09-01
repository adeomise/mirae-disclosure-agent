"""Pattern E (Event History) 통합 테스트.

질문 문자열에서 구조화 결과까지의 흐름을 본다. 합성 manifest와 합성 corpus만 사용하며
실제 공시 XML, 실제 기업명, 실제 수치는 쓰지 않는다.

시나리오마다 manifest 구성이 달라야 해서(원공시가 유일한 경우 / 복수인 경우 / 정정 연결이
모호한 경우) 공용 fixture 대신 이 파일 안에서 corpus를 만든다.

Parser 경계에 대한 메모:
    합성 corpus의 XML은 경로 해석만 검증하는 최소 파일이다. 본문 파싱 결과는 event.parser가
    보완된 뒤의 형태를 전제한 fixture로 대신한다. 원문 XML을 다시 열어 우회하지 않는다.
"""

from __future__ import annotations

import dataclasses
import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.handlers import event_history
from disclosure_agent.agent.handlers.event_history import (
    REASON_AMBIGUOUS_ROOT,
    REASON_NO_ORIGINAL_FILING,
    build_event_history_result,
    filing_date_window,
    handle_event_history,
)
from disclosure_agent.agent.normalization import (
    GAP_ROOT_CANDIDATE_DOC_IDS,
    normalize_event_history_result,
)
from disclosure_agent.agent.query_analyzer import (
    INTENT_EVENT_HISTORY,
    build_query_plan,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ChainPolicy,
    EventLinkResolutionStatus,
    EvidenceLocationType,
    ExecutionStatus,
)
from disclosure_agent.common.schemas import AgentExecutionResult
from disclosure_agent.event.contract_adapter import GAP_UNRESOLVED_LINKS, gap_kinds
from disclosure_agent.event.diff import FIELD_CONTRACT_AMOUNT, FIELD_CONTRACT_END_DATE

COMPANY = "테스트반도체(주)"

SUBTYPE = "단일판매공급계약체결"

# "2025년 6월에 공시한 공급계약"이 원공시를 지목한다.
QUESTION = "테스트반도체 2025년 6월에 공시한 공급계약, 나중에 바뀐 내용 있어?"

KNOWN_COMPANIES = ("테스트반도체",)


def _record(receipt_no: str, receipt_date: str, *, is_correction: bool) -> dict[str, Any]:
    prefix = "[기재정정]" if is_correction else ""

    return {
        "doc_id": f"exchange_{receipt_no}",
        "corp_code": "00000001",
        "corp_name": COMPANY,
        "stock_code": "000001",
        "doc_group": "exchange",
        "doc_subtype": SUBTYPE,
        "report_nm": f"{prefix}단일판매ㆍ공급계약체결",
        "is_correction": is_correction,
        "rcept_no": receipt_no,
        "rcept_dt": receipt_date,
        "base_year": None,
        "base_month": None,
        "file_path": f"raw/exchange/테스트반도체/{receipt_no}",
        "file_format": "xml",
        "n_files": 1,
    }


@pytest.fixture
def make_event_corpus(tmp_path: Path):
    """레코드 목록으로 manifest와 corpus를 만든다."""

    def _make(records: list[dict[str, Any]]) -> tuple[Path, Path]:
        root = tmp_path / "corpus"

        manifest_path = tmp_path / "manifest.jsonl"

        with manifest_path.open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")

                directory = root / unicodedata.normalize("NFD", record["file_path"])

                directory.mkdir(parents=True, exist_ok=True)

                (directory / f"{record['rcept_no']}.xml").write_text(
                    "<html/>", encoding="utf-8"
                )

        return manifest_path, root

    return _make


@pytest.fixture
def stub_parser(monkeypatch: pytest.MonkeyPatch, make_exchange_document):
    """
    본문 파싱을 합성 문서로 대체한다.

    documents는 접수번호 -> make_exchange_document 인자 dict.
    """

    def _install(documents: dict[str, dict[str, Any]]) -> None:
        built = {
            receipt_no: make_exchange_document(**kwargs)
            for receipt_no, kwargs in documents.items()
        }

        monkeypatch.setattr(
            event_history,
            "parse_exchange_document",
            lambda path: built[Path(path).stem],
        )

    return _install


# --------------------------------------------------------------------------
# 1. QueryPlan
# --------------------------------------------------------------------------


def test_q13_question_builds_event_history_plan() -> None:
    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_class == "event"
    assert plan.document_subtype == SUBTYPE
    assert plan.year == 2025
    assert plan.month == 6
    assert plan.chain_policy is ChainPolicy.FULL_CHAIN
    assert plan.metric is None
    assert plan.accounting_scope == "not_applicable"


def test_single_fact_question_is_unaffected() -> None:
    """이력 표현이 없으면 기존 경로 그대로다."""
    plan = build_query_plan(
        "테스트반도체 2025년 연결 기준 매출액이 얼마야?",
        known_companies=KNOWN_COMPANIES,
    )

    assert plan.intent == "single_fact"
    assert plan.chain_policy is None


def test_supply_contract_value_question_is_not_event_history() -> None:
    """유형 표현만으로 이력 조회로 끌려가지 않는다."""
    plan = build_query_plan(
        "테스트반도체 2025년 공급계약 매출액이 얼마야?",
        known_companies=KNOWN_COMPANIES,
    )

    assert plan.intent == "single_fact"


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2025, 6, ("20250601", "20250630")),
        (2025, 2, ("20250201", "20250228")),
        (2024, 2, ("20240201", "20240229")),
        (2025, None, ("20250101", "20251231")),
    ],
)
def test_filing_date_window(year: int, month: int | None, expected) -> None:
    assert filing_date_window(year, month) == expected


# --------------------------------------------------------------------------
# 2. 완결 체인
# --------------------------------------------------------------------------


@pytest.fixture
def complete_chain(make_event_corpus, stub_parser):
    """원공시 1건 + 같은 원공시를 가리키는 정정 2건."""
    manifest_path, corpus_root = make_event_corpus(
        [
            _record("E0001", "20250601", is_correction=False),
            _record("E0002", "20251008", is_correction=True),
            _record("E0003", "20251218", is_correction=True),
        ]
    )

    stub_parser(
        {
            "E0001": {},
            "E0002": {
                "original_filing_date": "2025-06-01",
                "correction_date": "2025-10-08",
                "changes": [("계약기간 종료일", "2026-01-31", "2026-06-30")],
            },
            "E0003": {
                "original_filing_date": "2025-06-01",
                "correction_date": "2025-12-18",
                "changes": [("계약금액(원)", "1,000,000,000", "1,500,000,000")],
            },
        }
    )

    return manifest_path, corpus_root


def test_complete_chain_is_answerable(complete_chain) -> None:
    manifest_path, corpus_root = complete_chain

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.status is AnswerabilityStatus.ANSWERABLE
    assert result.blocked is None
    assert result.history.chain_complete is True


def test_complete_chain_orders_documents_and_changes(complete_chain) -> None:
    manifest_path, corpus_root = complete_chain

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.history.document_ids == [
        "exchange_E0001",
        "exchange_E0002",
        "exchange_E0003",
    ]
    assert [change.field_name for change in result.history.changes] == [
        FIELD_CONTRACT_END_DATE,
        FIELD_CONTRACT_AMOUNT,
    ]


def test_corrections_outside_the_question_window_are_still_collected(
    complete_chain,
) -> None:
    """
    질문의 기간은 원공시를 지목하는 조건이지 체인의 범위가 아니다.

    6월 창으로 체인까지 자르면 10월·12월 정정이 빠져 '바뀐 것이 없다'가 된다.
    """
    manifest_path, corpus_root = complete_chain

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert len(result.history.document_ids) == 3


def test_complete_chain_links_are_resolved(complete_chain) -> None:
    manifest_path, corpus_root = complete_chain

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert [link.source_doc_id for link in result.links] == [
        "exchange_E0002",
        "exchange_E0003",
    ]
    assert all(
        link.resolution_status is EventLinkResolutionStatus.RESOLVED
        for link in result.links
    )
    assert all(link.resolved_doc_id == "exchange_E0001" for link in result.links)


def test_evidence_covers_every_document_in_the_chain(complete_chain) -> None:
    """원공시는 변경 항목이 없다. 문서 단위 근거가 없으면 출발점이 근거 없이 남는다."""
    manifest_path, corpus_root = complete_chain

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    cited = {item.doc_id for item in result.evidence}

    assert cited == {"exchange_E0001", "exchange_E0002", "exchange_E0003"}
    assert all(
        item.location_type is EvidenceLocationType.EVENT_FIELD
        for item in result.evidence
    )


def test_change_evidence_supports_before_and_after_values(complete_chain) -> None:
    """행 단위 Evidence가 원문 라벨과 before/after를 함께 보존한다."""
    manifest_path, corpus_root = complete_chain

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    evidence_by_id = {item.evidence_id: item for item in result.evidence}
    raw_field_by_name = {
        FIELD_CONTRACT_END_DATE: "계약기간 종료일",
        FIELD_CONTRACT_AMOUNT: "계약금액(원)",
    }

    assert result.history is not None
    for change in result.history.changes:
        assert change.before_evidence_id is not None
        assert change.after_evidence_id is not None
        assert change.before_evidence_id == change.after_evidence_id

        evidence = evidence_by_id[change.before_evidence_id]
        assert evidence.doc_id in result.history.document_ids
        assert evidence.raw_text is not None
        assert f"before: {change.before}" in evidence.raw_text
        assert f"after: {change.after}" in evidence.raw_text
        assert f"raw_field: {raw_field_by_name[change.field_name]}" in evidence.raw_text


def test_no_natural_language_answer_is_invented(complete_chain) -> None:
    """구조화 결과까지만 만든다. 빈 문자열로 형식을 맞추지 않는다."""
    manifest_path, corpus_root = complete_chain

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert not hasattr(result, "answer")
    assert "answer" not in result.to_dict()


def test_result_is_json_serializable(complete_chain) -> None:
    manifest_path, corpus_root = complete_chain

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    dumped = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))

    assert dumped["status"] == "ANSWERABLE"
    assert dumped["event_history"]["chain_complete"] is True


# --------------------------------------------------------------------------
# 3. 원공시가 모호한 경우
# --------------------------------------------------------------------------


def test_ambiguous_root_is_unresolved(make_event_corpus, stub_parser) -> None:
    """같은 달에 원공시가 2건이면 어느 계약의 이력인지 정할 수 없다."""
    manifest_path, corpus_root = make_event_corpus(
        [
            _record("E0001", "20250601", is_correction=False),
            _record("E0002", "20250612", is_correction=False),
        ]
    )

    stub_parser({"E0001": {}, "E0002": {}})

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.status is AnswerabilityStatus.UNRESOLVED
    assert result.blocked.reason == REASON_AMBIGUOUS_ROOT
    assert result.history is None
    assert set(result.root_candidate_doc_ids) == {"exchange_E0001", "exchange_E0002"}


def test_ambiguous_root_does_not_pick_one(make_event_corpus, stub_parser) -> None:
    """후보를 병기할 뿐 하나를 고르지 않는다."""
    manifest_path, corpus_root = make_event_corpus(
        [
            _record("E0001", "20250601", is_correction=False),
            _record("E0002", "20250612", is_correction=False),
        ]
    )

    stub_parser({"E0001": {}, "E0002": {}})

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert len(result.root_candidate_doc_ids) == 2
    assert result.evidence == ()


def test_no_original_filing_in_window_is_unanswerable(
    make_event_corpus,
    stub_parser,
) -> None:
    """다른 기간의 공시로 대신하지 않는다."""
    manifest_path, corpus_root = make_event_corpus(
        [_record("E0001", "20250301", is_correction=False)]
    )

    stub_parser({"E0001": {}})

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.status is AnswerabilityStatus.UNANSWERABLE
    assert result.blocked.reason == REASON_NO_ORIGINAL_FILING


# --------------------------------------------------------------------------
# 4. 연결이 일부 모호한 경우
# --------------------------------------------------------------------------


def test_partially_resolvable_chain_is_partially_answerable(
    make_event_corpus,
    stub_parser,
) -> None:
    """
    원공시는 확정됐고 일부 정정만 연결하지 못한 경우다.

    확보한 구간은 원문이 밝힌 연결만 따라간 것이라 신뢰할 수 있다. 확보분을 답하고 미해결분을
    밝힌다.
    """
    manifest_path, corpus_root = make_event_corpus(
        [
            _record("E0001", "20250601", is_correction=False),
            # 같은 날 제출된 정정 2건. 뒤 정정이 이 날짜를 가리키면 대상을 특정할 수 없다.
            _record("E0002", "20251008", is_correction=True),
            _record("E0003", "20251008", is_correction=True),
            _record("E0004", "20251218", is_correction=True),
        ]
    )

    stub_parser(
        {
            "E0001": {},
            "E0002": {
                "original_filing_date": "2025-06-01",
                "correction_date": "2025-10-08",
                "changes": [("계약기간 종료일", "2026-01-31", "2026-06-30")],
            },
            "E0003": {
                "original_filing_date": "2025-06-01",
                "correction_date": "2025-10-08",
                "changes": [("계약금액(원)", "1,000", "2,000")],
            },
            "E0004": {
                "original_filing_date": "2025-10-08",
                "correction_date": "2025-12-18",
                "changes": [("계약기간 종료일", "2026-06-30", "2026-09-30")],
            },
        }
    )

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert result.history.chain_complete is False

    # 확보한 구간은 그대로 답한다.
    assert result.history.document_ids == [
        "exchange_E0001",
        "exchange_E0002",
        "exchange_E0003",
    ]


def test_partial_chain_reports_what_it_could_not_link(
    make_event_corpus,
    stub_parser,
) -> None:
    """조용히 빼면 이력이 완결된 것처럼 보인다."""
    manifest_path, corpus_root = make_event_corpus(
        [
            _record("E0001", "20250601", is_correction=False),
            _record("E0002", "20251008", is_correction=True),
            _record("E0003", "20251008", is_correction=True),
            _record("E0004", "20251218", is_correction=True),
        ]
    )

    stub_parser(
        {
            "E0001": {},
            "E0002": {
                "original_filing_date": "2025-06-01",
                "correction_date": "2025-10-08",
            },
            "E0003": {
                "original_filing_date": "2025-06-01",
                "correction_date": "2025-10-08",
            },
            "E0004": {
                "original_filing_date": "2025-10-08",
                "correction_date": "2025-12-18",
            },
        }
    )

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert GAP_UNRESOLVED_LINKS in gap_kinds(result.conversion_gaps)

    ambiguous = [
        link
        for link in result.links
        if link.resolution_status is EventLinkResolutionStatus.AMBIGUOUS
    ]

    assert [link.source_doc_id for link in ambiguous] == ["exchange_E0004"]


# --------------------------------------------------------------------------
# 5. 공통 실행 Contract 정규화
#
# 이미 Contract 타입인 EventHistory / EventLink / Evidence는 다시 만들지 않는다. 재변환하면
# event.contract_adapter가 이미 내린 판정을 두 번 내리게 된다.
# --------------------------------------------------------------------------


def test_complete_chain_normalizes_to_answerable(complete_chain) -> None:
    manifest_path, corpus_root = complete_chain

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    internal = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    result = normalize_event_history_result(internal)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert result.execution_issue is None


def test_normalization_keeps_the_contract_objects_untouched(complete_chain) -> None:
    """같은 객체를 그대로 싣는다. 값을 다시 만들면 판정이 두 벌이 된다."""
    manifest_path, corpus_root = complete_chain

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    internal = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    result = normalize_event_history_result(internal)

    assert result.event_histories == [internal.history]
    assert result.event_links == list(internal.links)
    assert result.evidence == list(internal.evidence)


def test_incomplete_chain_keeps_its_partial_meaning(
    make_event_corpus,
    stub_parser,
) -> None:
    """
    확보한 구간은 답하고 미해결분을 밝힌다는 기존 의미를 유지한다.

    chain_complete=False를 기계가 읽을 수 있는 사유로 옮길 뿐, 새로 판정하지 않는다.
    """
    manifest_path, corpus_root = make_event_corpus(
        [
            _record("E0001", "20250601", is_correction=False),
            _record("E0002", "20251008", is_correction=True),
            _record("E0003", "20251008", is_correction=True),
            _record("E0004", "20251218", is_correction=True),
        ]
    )

    stub_parser(
        {
            "E0001": {},
            "E0002": {
                "original_filing_date": "2025-06-01",
                "correction_date": "2025-10-08",
            },
            "E0003": {
                "original_filing_date": "2025-06-01",
                "correction_date": "2025-10-08",
            },
            "E0004": {
                "original_filing_date": "2025-10-08",
                "correction_date": "2025-12-18",
            },
        }
    )

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = handle_event_history(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.INCOMPLETE_CHAIN
    assert result.event_histories[0].chain_complete is False

    # 멈춘 지점이 없는 판정이다. 없는 진단을 지어내지 않는다.
    assert result.answerability_detail is None


def test_conversion_gaps_stay_structured(
    make_event_corpus,
    stub_parser,
) -> None:
    """
    gap은 문자열로 합치지 않는다.

    dropped에 담긴 원본 값이 gap의 존재 이유다. 요약하면 남길 이유가 없어진다.
    """
    manifest_path, corpus_root = make_event_corpus(
        [
            _record("E0001", "20250601", is_correction=False),
            _record("E0002", "20251008", is_correction=True),
            _record("E0003", "20251008", is_correction=True),
            _record("E0004", "20251218", is_correction=True),
        ]
    )

    stub_parser(
        {
            "E0001": {},
            "E0002": {
                "original_filing_date": "2025-06-01",
                "correction_date": "2025-10-08",
                "changes": [("계약기간 종료일", "2026-01-31", "2026-06-30")],
            },
            "E0003": {
                "original_filing_date": "2025-06-01",
                "correction_date": "2025-10-08",
            },
            "E0004": {
                "original_filing_date": "2025-10-08",
                "correction_date": "2025-12-18",
            },
        }
    )

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    internal = build_event_history_result(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    result = normalize_event_history_result(internal)

    assert [gap.kind for gap in result.conversion_gaps] == [
        gap.kind for gap in internal.conversion_gaps
    ]
    assert [gap.dropped for gap in result.conversion_gaps] == [
        dict(gap.dropped) for gap in internal.conversion_gaps
    ]
    assert any(gap.kind == GAP_UNRESOLVED_LINKS for gap in result.conversion_gaps)

    unresolved = next(
        gap for gap in result.conversion_gaps if gap.kind == GAP_UNRESOLVED_LINKS
    )

    assert unresolved.dropped["unresolved_correction_receipt_nos"] == ["E0004"]


def test_no_original_filing_normalizes_to_not_found(
    make_event_corpus,
    stub_parser,
) -> None:
    """manifest 전체와 대조한 결과다. 실행은 끝났고 답할 것이 없을 뿐이다."""
    manifest_path, corpus_root = make_event_corpus(
        [_record("E0001", "20250301", is_correction=False)]
    )

    stub_parser({"E0001": {}})

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = handle_event_history(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert result.execution_issue is None

    # NOT_FOUND만으로는 no_matching_filing과 구분되지 않는다. 원문 진단이 그 구분을 남긴다.
    assert result.answerability_detail.reason == REASON_NO_ORIGINAL_FILING


def test_ambiguous_root_normalizes_to_ambiguous_match(
    make_event_corpus,
    stub_parser,
) -> None:
    """
    후보를 모두 찾았고 하나로 좁힐 근거가 없다고 판정했다. 판정 자체는 끝났다.

    AMBIGUOUS_LINK가 아니다. 그 값은 문서 사이의 '연결'이 모호하다는 뜻이고 event.linker가
    내리는 판정인데, 여기에는 EventLink가 하나도 없다. 모호한 것은 질문이 가리키는 대상
    문서이므로 AMBIGUOUS_MATCH다.
    """
    manifest_path, corpus_root = make_event_corpus(
        [
            _record("E0001", "20250601", is_correction=False),
            _record("E0002", "20250612", is_correction=False),
        ]
    )

    stub_parser({"E0001": {}, "E0002": {}})

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = handle_event_history(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert result.answerability_reason is not AnswerabilityReason.AMBIGUOUS_LINK
    assert result.execution_issue is None
    assert result.event_histories == []
    assert result.event_links == []

    assert result.answerability_detail.stage == "filing_discovery"
    assert result.answerability_detail.reason == REASON_AMBIGUOUS_ROOT


def test_ambiguous_root_keeps_the_candidate_documents(
    make_event_corpus,
    stub_parser,
) -> None:
    """
    후보를 병기할 자리가 없으면 gap에 남긴다. 하나를 골라 답하지 않는다.

    canonical reason과 그 진단은 어느 후보들 사이에서 모호했는지를 담지 못한다.
    """
    manifest_path, corpus_root = make_event_corpus(
        [
            _record("E0001", "20250601", is_correction=False),
            _record("E0002", "20250612", is_correction=False),
        ]
    )

    stub_parser({"E0001": {}, "E0002": {}})

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = handle_event_history(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    candidates = next(
        gap for gap in result.conversion_gaps if gap.kind == GAP_ROOT_CANDIDATE_DOC_IDS
    )

    assert set(candidates.dropped["root_candidate_doc_ids"]) == {
        "exchange_E0001",
        "exchange_E0002",
    }

    # 판정 근거는 손실이 아니므로 gap에는 남기지 않는다. 남는 gap은 담을 자리가 없는
    # 정보뿐이다.
    assert [gap.kind for gap in result.conversion_gaps] == [
        GAP_ROOT_CANDIDATE_DOC_IDS
    ]


def test_parse_failure_is_blocked_not_unanswerable(
    monkeypatch: pytest.MonkeyPatch,
    make_event_corpus,
) -> None:
    """문서를 열지 못했으면 답이 없는지조차 알지 못한다."""
    manifest_path, corpus_root = make_event_corpus(
        [_record("E0001", "20250601", is_correction=False)]
    )

    def _fail(path):
        raise ValueError("본문을 해석하지 못했습니다.")

    monkeypatch.setattr(event_history, "parse_exchange_document", _fail)

    plan = build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES)

    result = handle_event_history(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.answerability_reason is None
    assert result.answerability_detail is None
    assert result.execution_issue.stage == "parsing"
    assert result.execution_issue.reason == "parse_failed"
    assert "해석하지 못했습니다" in result.execution_issue.detail


def test_plan_warnings_are_preserved(complete_chain) -> None:
    """
    질의 해석 단계의 경고가 실행 결과까지 남아야 한다.

    기존 to_dict는 plan.warnings를 담지 않았다. 의미를 바꾸는 것이 아니라 누락된 진단
    정보를 보존하는 것이다.
    """
    manifest_path, corpus_root = complete_chain

    plan = dataclasses.replace(
        build_query_plan(QUESTION, known_companies=KNOWN_COMPANIES),
        warnings=("합성 경고",),
    )

    result = handle_event_history(
        plan, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.warnings == ["합성 경고"]


# --------------------------------------------------------------------------
# 6. Router 연결
# --------------------------------------------------------------------------


def test_router_dispatches_q13_question_to_pattern_e(complete_chain) -> None:
    manifest_path, corpus_root = complete_chain

    result = router.answer_question(
        QUESTION, corpus_root=corpus_root, manifest_path=manifest_path
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.event_histories[0].chain_complete is True


def test_router_still_dispatches_q01_question_to_pattern_a(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """Pattern E 등록이 Pattern A 경로를 건드리지 않는다."""
    result = router.answer_question(
        "테스트반도체 2025년 연결 기준 매출액이 얼마야?",
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.stage == "parsing"


def test_router_returns_one_contract_for_both_patterns(complete_chain) -> None:
    """
    Pattern이 달라도 공통 Contract 하나로 답한다.

    받는 쪽이 어느 Pattern이 실행됐는지 알아야 결과를 읽을 수 있으면, Pattern이 늘 때마다
    호출부가 함께 바뀐다.

    같은 코퍼스에 정기공시가 없으므로 Pattern A는 답할 것을 찾지 못한다. 결과가 달라도
    타입과 직렬화 경로는 같아야 한다.
    """
    manifest_path, corpus_root = complete_chain

    pattern_e = router.answer_question(
        QUESTION, corpus_root=corpus_root, manifest_path=manifest_path
    )

    pattern_a = router.answer_question(
        "테스트반도체 2025년 연결 기준 매출액이 얼마야?",
        corpus_root=corpus_root,
        manifest_path=manifest_path,
    )

    assert isinstance(pattern_e, AgentExecutionResult)
    assert isinstance(pattern_a, AgentExecutionResult)

    assert pattern_e.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert pattern_a.answerability_status is AnswerabilityStatus.UNANSWERABLE

    # 직렬화 경로도 Pattern에 관계없이 같아야 한다.
    for result in (pattern_a, pattern_e):
        dumped = result.model_dump(mode="json")

        assert json.loads(json.dumps(dumped, ensure_ascii=False)) == dumped


def test_router_reports_missing_manifest_for_pattern_e(tmp_path: Path) -> None:
    result = router.answer_question(
        QUESTION,
        corpus_root=tmp_path,
        manifest_path=tmp_path / "does_not_exist.jsonl",
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue.reason == "manifest_missing"
