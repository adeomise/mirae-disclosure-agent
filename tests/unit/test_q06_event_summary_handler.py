"""Q06 거래소공시 단건 요약 handler의 선택·시점·fail-closed 테스트.

합성 코퍼스를 쓰는 이유:
    실제 코퍼스의 LG유플러스 AIDC 체인은 원공시 1건뿐이라 최신 상태와 원공시 상태가 같은
    값이다. 두 시점이 실제로 갈리는지, 정정을 안전하게 해석하지 못할 때 낡은 값이 나가지
    않는지는 정정이 붙은 체인이 있어야 시험할 수 있다.

    실제 접수번호와 실제 공시 수치는 쓰지 않는다(AGENTS.md). 값은 전부 가공된 예시다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent.handlers import event_summary as event_summary_module
from disclosure_agent.agent.handlers.event_summary import (
    REASON_UNREPRESENTABLE_CORRECTION_CHANGE,
    REASON_UNSUPPORTED_COMPOSITE_CORRECTION,
    REASON_UNSUPPORTED_EVENT_SUMMARY_TYPE,
    REQUIRED_SUMMARY_FIELDS_BY_EVENT_TYPE,
    build_event_summary_result,
    handle_event_summary,
    judge_summary_answerability,
)
from disclosure_agent.agent.query_analyzer import (
    INTENT_EVENT_SUMMARY,
    QueryPlan,
    build_query_plan,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ChainPolicy,
    EventType,
    ExecutionStatus,
)
from disclosure_agent.common.schemas import (
    ResolvedEventState,
    ResolvedFieldValue,
)
from disclosure_agent.event.correction_fields import (
    classify_latest_state_blocking_correction,
)
from disclosure_agent.event.extractor import FACILITY_INVESTMENT_FIELDS

COMPANY = "테스트텔레콤"
SUBTYPE_FACILITY = "신규시설투자등"
SUBTYPE_SUPPLY = "단일판매공급계약체결"

# 질문이 가리키는 주제. 실제 지명·설비명을 쓰지 않는다.
SUBJECT_TOKENS = ("가상", "데이터센터")

ROOT_RECEIPT_NO = "SYNTHETIC90010001"
ROOT_DOC_ID = f"exchange_{ROOT_RECEIPT_NO}"
ROOT_DATE = "20250110"


# --------------------------------------------------------------------------
# 합성 코퍼스 만들기
# --------------------------------------------------------------------------


def facility_investment_body(
    *,
    target: str,
    purpose: str = "가상 수요 증가 대응",
    amount: str = "1,000",
    start_date: str = "2025-02-01",
    end_date: str = "2026-02-01",
    investment_type: str = "신규시설투자",
    correction: dict[str, Any] | None = None,
) -> str:
    """신규시설투자 공시 1건의 본문을 만든다.

    행 구조는 event.parser가 실제 서식에서 읽는 것과 같다(group | field | value).
    정정 블록은 XFormD8 표에만 담기며, 본문 표는 정정 후 값을 그대로 담는다. 실제 정정
    공시가 전체 서식을 다시 제출하는 것과 같은 모양이다.
    """
    correction_markup = ""

    if correction is not None:
        change_rows = "".join(
            f"<tr><td>{raw_field}</td><td>{before}</td><td>{after}</td></tr>"
            for raw_field, before, after in correction["changes"]
        )

        correction_markup = f"""
        <span>정정신고(보고)</span>
        <table id="XFormD8_Form0_Table1">
          <tr><td>정정일자</td><td>{correction["correction_date"]}</td></tr>
        </table>
        <table id="XFormD8_Form0_RepeatTable0">
          <tr><td>1. 정정관련 공시서류</td><td>신규시설투자등</td></tr>
          <tr><td>2. 정정관련 공시서류제출일</td><td>{correction["original_filing_date"]}</td></tr>
          <tr><td>3. 정정사유</td><td>{correction.get("reason", "투자 계획 변경")}</td></tr>
          <tr><td>정정항목</td><td>정정전</td><td>정정후</td></tr>
          {change_rows}
        </table>
        """

    return f"""
    {correction_markup}
    <table id="XFormD1_Form0_Table0">
      <tr><td>1. 투자구분</td><td>투자구분</td><td>{investment_type}</td></tr>
      <tr><td>1. 투자구분</td><td>투자대상</td><td>{target}</td></tr>
      <tr><td>2. 투자내역</td><td>투자금액(원)</td><td>{amount}</td></tr>
      <tr><td>2. 투자내역</td><td>자기자본(원)</td><td>10,000</td></tr>
      <tr><td>3. 투자목적</td><td>투자목적</td><td>{purpose}</td></tr>
      <tr><td>4. 투자기간</td><td>시작일</td><td>{start_date}</td></tr>
      <tr><td>4. 투자기간</td><td>종료일</td><td>{end_date}</td></tr>
      <tr><td>5. 이사회결의일(결정일)</td><td>이사회결의일</td><td>2025-01-09</td></tr>
      <tr><td>6. 사외이사 참석여부</td><td>불참(명)</td><td>-</td></tr>
      <tr><td>7. 공시유보 관련내용</td><td>유보사유</td><td>-</td></tr>
      <tr><td>8. 기타 투자판단과 관련한 중요사항</td><td>※ 관련공시</td><td>-</td></tr>
    </table>
    """


def supply_contract_body(*, counterparty: str, contract_name: str) -> str:
    """공급계약 공시 1건의 본문. 유형이 다른 후보가 섞였을 때를 시험한다."""
    return f"""
    <span>단일판매ㆍ공급계약 체결</span>
    <table id="XFormD1_Form0_Table0">
      <tr><td>1. 판매ㆍ공급계약구분</td><td>체결계약명</td><td>{contract_name}</td></tr>
      <tr><td>2. 계약내역</td><td>계약금액(원)</td><td>2,000</td></tr>
      <tr><td>3. 계약상대</td><td>계약상대</td><td>{counterparty}</td></tr>
      <tr><td>5. 계약기간</td><td>시작일</td><td>2025-03-01</td></tr>
      <tr><td>5. 계약기간</td><td>종료일</td><td>2026-03-01</td></tr>
    </table>
    """


def build_corpus(tmp_path: Path, filings: list[dict[str, Any]]) -> Path:
    """manifest와 본문 파일을 갖춘 합성 코퍼스를 만든다."""
    root = tmp_path / "corpus"
    records = []

    for filing in filings:
        receipt_no = filing["receipt_no"]
        subtype = filing.get("document_subtype", SUBTYPE_FACILITY)
        report_name = filing.get("report_name", subtype)
        file_path = f"raw/exchange/{COMPANY}/{receipt_no}"

        directory = root / file_path
        directory.mkdir(parents=True, exist_ok=True)

        (directory / f"{receipt_no}.xml").write_text(
            f"<html><head><title>{report_name}</title></head>"
            f"<body>{filing['body']}</body></html>",
            encoding="utf-8",
        )

        records.append(
            {
                "doc_id": f"exchange_{receipt_no}",
                "corp_code": "00009001",
                "corp_name": COMPANY,
                "stock_code": "090001",
                "doc_group": "exchange",
                "doc_subtype": subtype,
                "report_nm": report_name,
                "is_correction": filing.get("is_correction", False),
                "rcept_no": receipt_no,
                "rcept_dt": filing["receipt_date"],
                "base_year": None,
                "base_month": None,
                "file_path": file_path,
                "file_format": "xml",
                "n_files": 1,
            }
        )

    manifest = root / "manifest.jsonl"

    with manifest.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    return root


def root_filing(**overrides: Any) -> dict[str, Any]:
    body = facility_investment_body(
        target=overrides.pop("target", "가상 데이터센터 신규 구축"),
        **{
            key: overrides.pop(key)
            for key in ("purpose", "amount", "start_date", "end_date")
            if key in overrides
        },
    )

    return {
        "receipt_no": ROOT_RECEIPT_NO,
        "receipt_date": ROOT_DATE,
        "body": body,
        **overrides,
    }


def plan(
    *,
    chain_policy: ChainPolicy = ChainPolicy.LATEST_VALID,
    subject_tokens: tuple[str, ...] = SUBJECT_TOKENS,
    document_subtype: str | None = None,
    years: tuple[int, ...] = (),
) -> QueryPlan:
    return QueryPlan(
        question=f"{COMPANY} 가상 데이터센터 투자 공시 내용 요약해줘.",
        companies=(COMPANY,),
        years=years,
        intent=INTENT_EVENT_SUMMARY,
        document_class="event",
        document_subtype=document_subtype,
        accounting_scope="not_applicable",
        metric=None,
        chain_policy=chain_policy,
        event_subject_tokens=subject_tokens,
    )


# --------------------------------------------------------------------------
# 필수 요약 항목의 어휘가 추출 계층과 붙어 있는가
# --------------------------------------------------------------------------


def test_required_summary_fields_use_the_extractor_vocabulary() -> None:
    """필수 항목 이름이 event.extractor의 canonical field 어휘와 같다.

    두 이름이 갈리면 상태에는 값이 있는데 요약은 '항목이 없다'로 판정한다. 그 어긋남은
    결과만 봐서는 드러나지 않는다.
    """
    canonical = set(FACILITY_INVESTMENT_FIELDS.values())

    required = REQUIRED_SUMMARY_FIELDS_BY_EVENT_TYPE[EventType.FACILITY_INVESTMENT]

    assert set(required) <= canonical
    assert required == (
        "investment_target",
        "investment_amount",
        "investment_purpose",
        "investment_start_date",
        "investment_end_date",
    )


# --------------------------------------------------------------------------
# A/C. 내용으로 이벤트를 고른다
# --------------------------------------------------------------------------


def test_event_is_selected_by_question_content_not_by_identifier(tmp_path: Path) -> None:
    """같은 기업·같은 유형의 공시가 여럿이어도 주제어로 1건을 고른다.

    문서 식별자를 쓰지 않는다는 것을 이 배치가 보인다. 접수번호로 골랐다면 다른 주제를 물은
    질문에도 같은 문서가 나온다.
    """
    corpus = build_corpus(
        tmp_path,
        [
            root_filing(),
            {
                "receipt_no": "SYNTHETIC90010002",
                "receipt_date": "20250115",
                "body": facility_investment_body(target="가상 물류센터 증설"),
            },
        ],
    )

    result = build_event_summary_result(plan(), corpus_root=corpus)

    assert result.status is AnswerabilityStatus.ANSWERABLE
    assert result.state is not None
    assert result.state.fields["investment_target"].value == "가상 데이터센터 신규 구축"
    assert result.root_candidate_doc_ids == (ROOT_DOC_ID,)


def test_a_different_subject_selects_a_different_event(tmp_path: Path) -> None:
    """선택이 질문에서 온다는 것을 반대 방향으로도 확인한다."""
    corpus = build_corpus(
        tmp_path,
        [
            root_filing(),
            {
                "receipt_no": "SYNTHETIC90010002",
                "receipt_date": "20250115",
                "body": facility_investment_body(target="가상 물류센터 증설"),
            },
        ],
    )

    result = build_event_summary_result(
        plan(subject_tokens=("가상", "물류센터")),
        corpus_root=corpus,
    )

    assert result.state is not None
    assert result.state.fields["investment_target"].value == "가상 물류센터 증설"


def test_all_required_summary_fields_carry_field_level_evidence(tmp_path: Path) -> None:
    """드러낸 값마다 근거가 응답 안에 함께 있다."""
    corpus = build_corpus(tmp_path, [root_filing()])

    execution = handle_event_summary(plan(), corpus_root=corpus)

    assert execution.execution_status is ExecutionStatus.COMPLETED
    assert execution.answerability_status is AnswerabilityStatus.ANSWERABLE

    state = execution.resolved_event_states[0]
    evidence_ids = {item.evidence_id for item in execution.evidence}

    for field_name in REQUIRED_SUMMARY_FIELDS_BY_EVENT_TYPE[EventType.FACILITY_INVESTMENT]:
        resolved = state.fields[field_name]

        assert resolved.evidence_ids
        assert set(resolved.evidence_ids) <= evidence_ids
        assert resolved.source_doc_id == ROOT_DOC_ID


# --------------------------------------------------------------------------
# B. 최신 유효 상태와 원공시 상태가 갈린다
# --------------------------------------------------------------------------


def corrected_chain(tmp_path: Path) -> Path:
    """원공시 1건과 투자금액을 고친 정정 1건."""
    return build_corpus(
        tmp_path,
        [
            root_filing(amount="1,000"),
            {
                "receipt_no": "SYNTHETIC90010009",
                "receipt_date": "20250620",
                "is_correction": True,
                "report_name": "[기재정정]신규시설투자등",
                "body": facility_investment_body(
                    target="가상 데이터센터 신규 구축",
                    amount="1,500",
                    correction={
                        "correction_date": "2025-06-20",
                        "original_filing_date": "2025-01-10",
                        "changes": [("2. 투자내역 - 투자금액(원)", "1,000", "1,500")],
                    },
                ),
            },
        ],
    )


def test_latest_effective_state_reflects_the_correction(tmp_path: Path) -> None:
    corpus = corrected_chain(tmp_path)

    result = build_event_summary_result(
        plan(chain_policy=ChainPolicy.LATEST_VALID),
        corpus_root=corpus,
    )

    assert result.status is AnswerabilityStatus.ANSWERABLE
    assert result.state is not None
    assert result.state.fields["investment_amount"].value == 1500
    assert (
        result.state.fields["investment_amount"].source_doc_id
        == "exchange_SYNTHETIC90010009"
    )
    assert result.state.as_of.isoformat() == "2025-06-20"


def test_original_state_keeps_the_root_values(tmp_path: Path) -> None:
    corpus = corrected_chain(tmp_path)

    result = build_event_summary_result(
        plan(chain_policy=ChainPolicy.ORIGINAL_ONLY),
        corpus_root=corpus,
    )

    assert result.status is AnswerabilityStatus.ANSWERABLE
    assert result.state is not None
    assert result.state.fields["investment_amount"].value == 1000
    assert result.state.fields["investment_amount"].source_doc_id == ROOT_DOC_ID
    assert result.state.as_of.isoformat() == "2025-01-10"


def test_correction_is_the_same_event_chain(tmp_path: Path) -> None:
    """정정본이 독립 이벤트로 세어지지 않는다.

    독립 후보로 세면 원공시 1건짜리 질문이 후보 2건으로 보여 ambiguous가 된다.
    """
    corpus = corrected_chain(tmp_path)

    latest = build_event_summary_result(plan(), corpus_root=corpus)
    original = build_event_summary_result(
        plan(chain_policy=ChainPolicy.ORIGINAL_ONLY),
        corpus_root=corpus,
    )

    assert latest.state is not None
    assert original.state is not None
    assert latest.state.event_id == original.state.event_id
    assert latest.root_candidate_doc_ids == original.root_candidate_doc_ids == (ROOT_DOC_ID,)


# --------------------------------------------------------------------------
# D. 요약과 무관한 유보는 판정을 낮추지 않는다
# --------------------------------------------------------------------------


def test_irrelevant_blank_fields_do_not_downgrade_the_summary(tmp_path: Path) -> None:
    """'-'로 비워 둔 다른 항목이 있어도 ANSWERABLE이다.

    합성 본문에는 사외이사 불참(명), 유보사유, 관련공시가 모두 '-'다. 이것들은 질문이 묻지
    않은 항목이며, 필수 집합 밖의 빈칸이 판정을 낮추면 거의 모든 공시가 부분 답변이 된다.
    """
    corpus = build_corpus(tmp_path, [root_filing()])

    execution = handle_event_summary(plan(), corpus_root=corpus)

    assert execution.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert execution.answerability_reason is None


# --------------------------------------------------------------------------
# E. 필수 항목이 원문에서 유보된 경우
# --------------------------------------------------------------------------


def test_partially_withheld_required_field_uses_withheld_in_source(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path, [root_filing(amount="-")])

    execution = handle_event_summary(plan(), corpus_root=corpus)

    assert execution.execution_status is ExecutionStatus.COMPLETED
    assert execution.answerability_status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert execution.answerability_reason is AnswerabilityReason.WITHHELD_IN_SOURCE
    assert execution.resolved_event_states


def test_fully_withheld_required_fields_use_withheld_in_source(tmp_path: Path) -> None:
    """필수 항목 전체가 유보면 UNANSWERABLE이다(open_decisions.md D9 case B)."""
    corpus = build_corpus(
        tmp_path,
        [
            root_filing(
                target="-",
                purpose="-",
                amount="-",
                start_date="-",
                end_date="-",
            )
        ],
    )

    result = build_event_summary_result(
        plan(subject_tokens=("신규시설투자",)),
        corpus_root=corpus,
    )

    assert result.status is AnswerabilityStatus.UNANSWERABLE
    assert result.reason is AnswerabilityReason.WITHHELD_IN_SOURCE
    assert set(result.withheld_fields) == set(
        REQUIRED_SUMMARY_FIELDS_BY_EVENT_TYPE[EventType.FACILITY_INVESTMENT]
    )


# --------------------------------------------------------------------------
# E-2. 값만으로는 답한 것이 아니다 — 필수 항목의 근거 불변식
# --------------------------------------------------------------------------

REQUIRED_FACILITY_FIELDS = REQUIRED_SUMMARY_FIELDS_BY_EVENT_TYPE[
    EventType.FACILITY_INVESTMENT
]


def resolved_state(
    *,
    evidence_ids_by_field: dict[str, list[str]],
) -> ResolvedEventState:
    """필수 항목이 전부 값을 갖는 상태. 근거 식별자만 항목별로 달리 준다.

    값 쪽은 고정한다. 이 묶음이 가르는 것은 '값이 있는가'가 아니라 '그 값에 근거가 붙어
    응답 안에 있는가'이며, 값을 함께 흔들면 무엇이 판정을 바꿨는지 알 수 없다.
    """
    return ResolvedEventState(
        event_id=ROOT_DOC_ID,
        event_type=EventType.FACILITY_INVESTMENT,
        company=COMPANY,
        as_of=date(2025, 1, 10),
        fields={
            field_name: ResolvedFieldValue(
                value="가상 값",
                source_doc_id=ROOT_DOC_ID,
                evidence_ids=evidence_ids_by_field[field_name],
            )
            for field_name in REQUIRED_FACILITY_FIELDS
        },
    )


def grounded_ids(missing_for: str | None = None, *, dangling: bool = False) -> tuple[
    dict[str, list[str]],
    set[str],
]:
    """필드별 근거 식별자와, 응답이 실제로 싣는 근거 식별자 집합을 함께 만든다.

    missing_for가 가리키는 항목만 근거를 잃는다. dangling이면 식별자는 있으나 그 식별자가
    응답의 Evidence에 없다.
    """
    by_field = {
        field_name: [f"{ROOT_DOC_ID}:{field_name}"] for field_name in REQUIRED_FACILITY_FIELDS
    }

    available = set()

    for field_name, ids in by_field.items():
        if field_name == missing_for:
            if dangling:
                by_field[field_name] = ["evidence-that-does-not-exist"]

            else:
                by_field[field_name] = []

            continue

        available.update(ids)

    return by_field, available


def test_required_field_without_evidence_ids_is_not_answerable() -> None:
    """A. 값은 있는데 근거 식별자가 비어 있으면 해결된 것으로 세지 않는다.

    ResolvedFieldValue.evidence_ids는 비어 있을 수 있고, 최신 상태 확정은 그런 값을 만들 수
    있다. 값만 보고 세면 근거 없는 값이 ANSWERABLE로 나가며, 받는 쪽은 그 값을 확인할 방법이
    없는 채로 확인된 값과 구별하지 못한다.
    """
    by_field, available = grounded_ids(missing_for="investment_amount")

    status, reason, missing, withheld = judge_summary_answerability(
        resolved_state(evidence_ids_by_field=by_field),
        REQUIRED_FACILITY_FIELDS,
        available_evidence_ids=available,
    )

    assert status is not AnswerabilityStatus.ANSWERABLE
    assert status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert reason is AnswerabilityReason.PARTIAL_SOURCE_SUPPORT
    assert missing == ("investment_amount",)
    # 유보로 세지 않는다. 유보는 원문이 값을 공개하지 않았다는 원문 쪽 사실이고, 여기서
    # 일어난 일은 이 파이프라인이 값을 근거에 잇지 못한 것이다.
    assert withheld == ()


def test_required_field_with_dangling_evidence_id_is_not_answerable() -> None:
    """B. 식별자가 있어도 그것이 가리키는 Evidence가 응답에 없으면 세지 않는다.

    식별자 유무만 보면 이 경우가 통과한다. _state_evidence는 source_doc_id로 찾지 못한
    문서나 그 문서에 없는 식별자를 건너뛰므로, 상태가 가리키는 근거가 응답에 실리지 않는
    상태가 실제로 만들어질 수 있다.
    """
    by_field, available = grounded_ids(missing_for="investment_amount", dangling=True)

    status, reason, missing, withheld = judge_summary_answerability(
        resolved_state(evidence_ids_by_field=by_field),
        REQUIRED_FACILITY_FIELDS,
        available_evidence_ids=available,
    )

    assert status is not AnswerabilityStatus.ANSWERABLE
    assert status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert reason is AnswerabilityReason.PARTIAL_SOURCE_SUPPORT
    assert missing == ("investment_amount",)
    assert withheld == ()


def test_required_fields_with_valid_evidence_stay_answerable() -> None:
    """C. 값과 근거가 모두 갖춰지면 기존 판정 그대로다.

    불변식이 답할 수 있는 질문까지 막지 않는다는 것을 고정한다.
    """
    by_field, available = grounded_ids()

    status, reason, missing, withheld = judge_summary_answerability(
        resolved_state(evidence_ids_by_field=by_field),
        REQUIRED_FACILITY_FIELDS,
        available_evidence_ids=available,
    )

    assert status is AnswerabilityStatus.ANSWERABLE
    assert reason is None
    assert missing == ()
    assert withheld == ()


def test_no_required_field_has_evidence_is_unanswerable() -> None:
    """필수 항목 전체가 근거를 잃으면 부분 답변이 아니라 UNANSWERABLE이다.

    미확보가 전부이므로 사유는 insufficient_evidence다. 여기서도 새 사유를 만들지 않는다.
    """
    by_field = {field_name: [] for field_name in REQUIRED_FACILITY_FIELDS}

    status, reason, missing, withheld = judge_summary_answerability(
        resolved_state(evidence_ids_by_field=by_field),
        REQUIRED_FACILITY_FIELDS,
        available_evidence_ids=set(),
    )

    assert status is AnswerabilityStatus.UNANSWERABLE
    assert reason is AnswerabilityReason.INSUFFICIENT_EVIDENCE
    assert set(missing) == set(REQUIRED_FACILITY_FIELDS)
    assert withheld == ()


def test_handler_judges_against_the_evidence_it_actually_carries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """판정이 응답에 실제로 실리는 근거를 본다.

    앞의 세 테스트는 판정 함수만 고정한다. 그것만으로는 handler가 그 함수에 무엇을 넘기는지
    모른다. 근거를 하나도 싣지 못하는 상황을 만들어, 값이 전부 갖춰진 실행이 그래도
    ANSWERABLE로 나가지 않는지 실행 경로에서 확인한다.

    이 배치가 없으면 판정과 응답이 서로 다른 것을 보게 되는 회귀를 잡지 못한다.
    """
    corpus = build_corpus(tmp_path, [root_filing()])

    monkeypatch.setattr(event_summary_module, "_state_evidence", lambda state, adapted: ())

    execution = handle_event_summary(plan(), corpus_root=corpus)

    assert execution.execution_status is ExecutionStatus.COMPLETED
    assert execution.resolved_event_states
    assert not execution.evidence
    assert execution.answerability_status is not AnswerabilityStatus.ANSWERABLE
    assert execution.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert execution.answerability_reason is AnswerabilityReason.INSUFFICIENT_EVIDENCE


# --------------------------------------------------------------------------
# F. 대상을 좁히지 못하면 fail closed
# --------------------------------------------------------------------------


def test_ambiguous_event_selection_fails_closed(tmp_path: Path) -> None:
    corpus = build_corpus(
        tmp_path,
        [
            root_filing(),
            {
                "receipt_no": "SYNTHETIC90010003",
                "receipt_date": "20250320",
                "body": facility_investment_body(target="가상 데이터센터 2단계 구축"),
            },
        ],
    )

    execution = handle_event_summary(plan(), corpus_root=corpus)

    assert execution.execution_status is ExecutionStatus.COMPLETED
    assert execution.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert execution.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert not execution.resolved_event_states


def test_unknown_subject_is_not_answered_with_another_disclosure(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path, [root_filing()])

    execution = handle_event_summary(
        plan(subject_tokens=("가상", "반도체공장")),
        corpus_root=corpus,
    )

    assert execution.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert execution.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert not execution.resolved_event_states


def test_unsupported_event_type_is_blocked_not_summarized(tmp_path: Path) -> None:
    """필수 요약 집합이 없는 유형은 항목을 골라 요약하지 않고 막는다."""
    corpus = build_corpus(
        tmp_path,
        [
            {
                "receipt_no": "SYNTHETIC90010004",
                "receipt_date": "20250401",
                "document_subtype": SUBTYPE_SUPPLY,
                "report_name": "단일판매ㆍ공급계약체결",
                "body": supply_contract_body(
                    counterparty="가상상사",
                    contract_name="가상 데이터센터 장비 공급",
                ),
            }
        ],
    )

    execution = handle_event_summary(plan(), corpus_root=corpus)

    assert execution.execution_status is ExecutionStatus.BLOCKED
    assert execution.execution_issue is not None
    assert execution.execution_issue.reason == REASON_UNSUPPORTED_EVENT_SUMMARY_TYPE
    assert execution.answerability_status is None


# --------------------------------------------------------------------------
# G. 정정을 안전하게 해석하지 못하면 낡은 값을 내보내지 않는다
# --------------------------------------------------------------------------


def test_incomplete_chain_does_not_fall_back_to_stale_root_values(tmp_path: Path) -> None:
    """정정 대상을 특정하지 못한 체인에서 최신 상태를 만들지 않는다.

    같은 날 제출된 같은 유형의 공시가 둘이라 정정이 어느 쪽을 고쳤는지 알 수 없다. 그 상태로
    원공시 값을 '최신 유효 상태'로 내보내면, 이미 정정된 값이 지금 값으로 나간다.
    """
    corpus = build_corpus(
        tmp_path,
        [
            root_filing(amount="1,000"),
            {
                # 원공시와 같은 날 제출된 다른 투자 건. 주제어가 달라 루트 선택은 갈린다.
                "receipt_no": "SYNTHETIC90010005",
                "receipt_date": ROOT_DATE,
                "body": facility_investment_body(target="가상 물류센터 증설"),
            },
            {
                "receipt_no": "SYNTHETIC90010009",
                "receipt_date": "20250620",
                "is_correction": True,
                "report_name": "[기재정정]신규시설투자등",
                "body": facility_investment_body(
                    target="가상 데이터센터 신규 구축",
                    amount="1,500",
                    correction={
                        "correction_date": "2025-06-20",
                        "original_filing_date": "2025-01-10",
                        "changes": [("2. 투자내역 - 투자금액(원)", "1,000", "1,500")],
                    },
                ),
            },
        ],
    )

    execution = handle_event_summary(plan(), corpus_root=corpus)

    assert execution.execution_status is ExecutionStatus.COMPLETED
    assert execution.answerability_status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert execution.answerability_reason is AnswerabilityReason.INCOMPLETE_CHAIN
    assert not execution.resolved_event_states
    assert execution.event_histories
    assert execution.event_histories[0].chain_complete is False


def test_original_state_survives_an_incomplete_chain(tmp_path: Path) -> None:
    """원공시 시점을 물은 질문은 이후 정정의 연결 실패에 막히지 않는다.

    원공시에 무엇이 적혀 있었는지는 원공시 문서 하나가 정한다.
    """
    corpus = build_corpus(
        tmp_path,
        [
            root_filing(amount="1,000"),
            {
                "receipt_no": "SYNTHETIC90010005",
                "receipt_date": ROOT_DATE,
                "body": facility_investment_body(target="가상 물류센터 증설"),
            },
            {
                "receipt_no": "SYNTHETIC90010009",
                "receipt_date": "20250620",
                "is_correction": True,
                "report_name": "[기재정정]신규시설투자등",
                "body": facility_investment_body(
                    target="가상 데이터센터 신규 구축",
                    amount="1,500",
                    correction={
                        "correction_date": "2025-06-20",
                        "original_filing_date": "2025-01-10",
                        "changes": [("2. 투자내역 - 투자금액(원)", "1,000", "1,500")],
                    },
                ),
            },
        ],
    )

    result = build_event_summary_result(
        plan(chain_policy=ChainPolicy.ORIGINAL_ONLY),
        corpus_root=corpus,
    )

    assert result.status is AnswerabilityStatus.ANSWERABLE
    assert result.state is not None
    assert result.state.fields["investment_amount"].value == 1000


def corpus_with_unnamed_correction(tmp_path: Path, raw_field: str) -> Path:
    """이름이 붙지 않는 정정 항목 하나만 다른 체인을 만든다.

    event.diff는 EventHistory가 지원하는 canonical 필드에만 이름을 붙이므로, 아래에 쓰는
    라벨들은 모두 change.field=None으로 남는다. 갈리는 것은 그 라벨이 공유 어휘로
    해석되는지, 해석된다면 Q06의 필수 항목인지다.
    """
    return build_corpus(
        tmp_path,
        [
            root_filing(),
            {
                "receipt_no": "SYNTHETIC90010009",
                "receipt_date": "20250620",
                "is_correction": True,
                "report_name": "[기재정정]신규시설투자등",
                "body": facility_investment_body(
                    target="가상 데이터센터 신규 구축",
                    correction={
                        "correction_date": "2025-06-20",
                        "original_filing_date": "2025-01-10",
                        "changes": [(raw_field, "이전 값", "이후 값")],
                    },
                ),
            },
        ],
    )


@pytest.mark.parametrize(
    "raw_field",
    (
        # 필수 항목인데 EventHistory 어휘에 없어 이름이 붙지 않는다.
        "투자대상",
        "3. 투자목적",
        # 공유 어휘로도 해석되지 않는다. 무엇이 바뀌었는지 알지 못한다.
        "알 수 없는 항목",
    ),
)
def test_unnamed_correction_of_a_required_field_fails_closed(
    tmp_path: Path,
    raw_field: str,
) -> None:
    """이름을 확정하지 못한 정정이 있으면 최신 상태를 만들지 않는다.

    resolve_latest_event_state는 이름 없는 변경을 건너뛴다. 그대로 두면 정정된 항목의
    원공시 값이 '최신 유효 상태'라는 이름으로 나가고, 결과만 봐서는 그것이 낡았다는 사실이
    드러나지 않는다.
    """
    execution = handle_event_summary(
        plan(),
        corpus_root=corpus_with_unnamed_correction(tmp_path, raw_field),
    )

    assert execution.execution_status is ExecutionStatus.BLOCKED
    assert execution.execution_issue is not None
    assert execution.execution_issue.reason == REASON_UNREPRESENTABLE_CORRECTION_CHANGE
    assert not execution.resolved_event_states


@pytest.mark.parametrize(
    "raw_field",
    (
        # 공유 어휘로 해석되고 Q06의 필수 항목이 아니다.
        "1. 투자구분",
        "8. 기타 투자판단과 관련한 중요사항",
    ),
)
def test_unnamed_correction_outside_the_summary_contract_is_allowed(
    tmp_path: Path,
    raw_field: str,
) -> None:
    """요약 대상이 아닌 항목의 정정은 최신 상태를 막지 않는다.

    이 경우까지 막으면 답할 수 있는 질문이 무관한 정정 하나 때문에 막힌다.
    """
    execution = handle_event_summary(
        plan(),
        corpus_root=corpus_with_unnamed_correction(tmp_path, raw_field),
    )

    assert execution.execution_status is ExecutionStatus.COMPLETED
    assert execution.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert execution.resolved_event_states


# --------------------------------------------------------------------------
# 질문 문자열에서 끝까지
# --------------------------------------------------------------------------


def test_question_string_reaches_the_summary_through_the_router(tmp_path: Path) -> None:
    """질의 해석과 handler가 실제로 이어져 있는지 확인한다."""
    from disclosure_agent.agent.router import answer_question

    corpus = build_corpus(tmp_path, [root_filing()])

    execution = answer_question(
        f"{COMPANY} 가상 데이터센터 투자 공시 내용 요약해줘.",
        corpus_root=corpus,
    )

    assert execution.execution_status is ExecutionStatus.COMPLETED
    assert execution.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert len(execution.resolved_event_states) == 1
    assert len(execution.events) == 1


def test_yearless_question_is_planned_without_a_synthesized_year(tmp_path: Path) -> None:
    """오늘 날짜에서 연도를 만들어 채우지 않는다."""
    built = build_query_plan(
        f"{COMPANY} 가상 데이터센터 투자 공시 내용 요약해줘.",
        (COMPANY,),
    )

    assert built.years == ()


# --------------------------------------------------------------------------
# 공개 응답까지 — 확정한 항목이 문장과 구조화 결과에 함께 나간다
# --------------------------------------------------------------------------


def test_answer_sentence_reports_every_required_summary_field(tmp_path: Path) -> None:
    """요약 항목이 문장에서 사라지지 않는다.

    구조화 결과에만 있고 문장에 없으면, 답을 문장으로만 읽는 쪽에서는 답하지 않은 것과 같다.
    """
    from disclosure_agent.agent.answer_builder import build_answer_response

    corpus = build_corpus(tmp_path, [root_filing()])

    response = build_answer_response(handle_event_summary(plan(), corpus_root=corpus))

    assert response.status is AnswerabilityStatus.ANSWERABLE
    assert response.reason is None

    for expected in (
        "가상 데이터센터 신규 구축",
        "1000원",
        "가상 수요 증가 대응",
        "2025-02-01",
        "2026-02-01",
        "2025-01-10",
    ):
        assert expected in response.answer

    assert len(response.resolved_event_states) == 1
    assert response.evidence


def test_public_response_carries_the_resolved_state(tmp_path: Path) -> None:
    """공개 응답에 상태를 실어야 값과 근거의 연결이 남는다."""
    from disclosure_agent.agent.answer_builder import build_answer_response

    corpus = build_corpus(tmp_path, [root_filing()])

    response = build_answer_response(handle_event_summary(plan(), corpus_root=corpus))

    state = response.resolved_event_states[0]
    evidence_ids = {item.evidence_id for item in response.evidence}

    for resolved in state.fields.values():
        assert set(resolved.evidence_ids) <= evidence_ids


def test_withheld_required_field_is_omitted_from_the_sentence(tmp_path: Path) -> None:
    """유보된 항목을 지어내지 않고, 남은 항목만 말한 뒤 한계를 밝힌다."""
    from disclosure_agent.agent.answer_builder import build_answer_response

    corpus = build_corpus(tmp_path, [root_filing(amount="-")])

    response = build_answer_response(handle_event_summary(plan(), corpus_root=corpus))

    assert response.status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert response.reason is AnswerabilityReason.WITHHELD_IN_SOURCE
    assert "투자 금액" not in response.answer
    assert "확인하지 못했습니다" in response.answer


def test_incomplete_chain_answer_does_not_state_summary_values(tmp_path: Path) -> None:
    """최신 상태를 확정하지 못한 답변에 원공시 값이 실리지 않는다."""
    from disclosure_agent.agent.answer_builder import build_answer_response

    corpus = build_corpus(
        tmp_path,
        [
            root_filing(amount="1,000"),
            {
                "receipt_no": "SYNTHETIC90010005",
                "receipt_date": ROOT_DATE,
                "body": facility_investment_body(target="가상 물류센터 증설"),
            },
            {
                "receipt_no": "SYNTHETIC90010009",
                "receipt_date": "20250620",
                "is_correction": True,
                "report_name": "[기재정정]신규시설투자등",
                "body": facility_investment_body(
                    target="가상 데이터센터 신규 구축",
                    amount="1,500",
                    correction={
                        "correction_date": "2025-06-20",
                        "original_filing_date": "2025-01-10",
                        "changes": [("2. 투자내역 - 투자금액(원)", "1,000", "1,500")],
                    },
                ),
            },
        ],
    )

    response = build_answer_response(handle_event_summary(plan(), corpus_root=corpus))

    assert response.status is AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert response.reason is AnswerabilityReason.INCOMPLETE_CHAIN
    assert not response.resolved_event_states
    assert "1000원" not in response.answer
    assert "1500원" not in response.answer


def test_grounded_payload_leaves_the_summary_to_the_deterministic_path(
    tmp_path: Path,
) -> None:
    """요약 결과는 외부 모델에 넘기지 않는다.

    payload에는 상태의 값들을 담을 자리가 없다. 그대로 넘기면 모델이 값을 하나도 받지 못한
    채 요약 문장을 지어낸다.
    """
    from disclosure_agent.agent.grounded_payload import (
        PayloadNotGroundableError,
        build_grounded_payload,
    )

    corpus = build_corpus(tmp_path, [root_filing()])

    execution = handle_event_summary(plan(), corpus_root=corpus)

    with pytest.raises(PayloadNotGroundableError, match="ResolvedEventState"):
        build_grounded_payload(execution, question="테스트 질문")


# --------------------------------------------------------------------------
# 조합형 정정은 공유 이벤트 계층의 판정을 그대로 쓴다(PR #98)
# --------------------------------------------------------------------------

# 공유 계층이 신규시설투자에 대해 분류해 둔 원문 라벨. 목록의 정본은
# event.correction_fields.LATEST_STATE_BLOCKING_CORRECTIONS이며, 아래 테스트가 그 사실을
# 함께 확인한다. 여기 적힌 것은 문서에 실제로 실리는 표기다.
FACILITY_COMPOSITE_LABELS = (
    "투자내역 / 투자금액(원) / 자기자본대비(%)",
    "투자금액(원) - 자기자본대비(%)",
)


@pytest.mark.parametrize("raw_field", FACILITY_COMPOSITE_LABELS)
def test_composite_correction_is_judged_by_the_shared_event_layer(
    tmp_path: Path,
    raw_field: str,
) -> None:
    """조합형 정정의 사유는 공유 계층이 정한다.

    Q06의 지역 가드가 이 라벨을 앞질러 잡으면 분류(category)가 결과에서 사라지고, "원문이
    여러 항목을 한 행에 묶었다"와 "이름을 못 붙였다"가 한 사유로 뭉개진다. 두 상황은 후속
    조치가 다르다.

    앞의 assert가 이 라벨의 정본이 공유 계층이라는 사실을 함께 고정한다. 목록을 handler로
    옮겨 적으면 공유 계층이 항목을 늘릴 때 이쪽만 뒤처진다.
    """
    assert (
        classify_latest_state_blocking_correction(
            raw_field,
            event_type=EventType.FACILITY_INVESTMENT.value,
        )
        is not None
    )

    execution = handle_event_summary(
        plan(),
        corpus_root=corpus_with_unnamed_correction(tmp_path, raw_field),
    )

    assert execution.execution_status is ExecutionStatus.BLOCKED
    assert execution.execution_issue is not None
    assert execution.execution_issue.reason == REASON_UNSUPPORTED_COMPOSITE_CORRECTION
    assert execution.execution_issue.reason != REASON_UNREPRESENTABLE_CORRECTION_CHANGE
    assert "source_composite" in execution.execution_issue.detail
    assert not execution.resolved_event_states
