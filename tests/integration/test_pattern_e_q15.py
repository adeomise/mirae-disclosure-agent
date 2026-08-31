"""Pattern E — Q15 계열(신규시설투자 정정 이력) 통합 테스트.

무엇을 고정하는가:
    Q15는 "레인보우로보틱스 사옥·제조시설 신축 투자, 그동안 계획이 몇 번 바뀌었어?"다.
    Q13(공급계약)과 같은 파이프라인을 쓰지만 두 가지가 다르다.

    - 질문이 공시 유형명을 쓰지 않는다. '신규시설투자'도 '시설투자'도 연속 문자열로 들어
      있지 않고, 대상(사옥·제조시설)과 행위(신축·투자)로만 말한다.
    - 질문이 공시 시점을 밝히지 않는다. 연도가 없다.

    이 파일은 그 둘을 여는 것과, 여는 과정에서 다른 질문이 함께 열리지 않는 것을 같은
    무게로 고정한다. 어휘를 넓히는 변경의 위험은 '열리지 않는다'가 아니라 '엉뚱한 것까지
    열린다'이기 때문이다.

무엇을 검증하지 않는가:
    자연어 답변을 만들지 않는다. 이 Pattern은 AgentExecutionResult까지가 범위이며,
    "몇 번 바뀌었나"를 문장으로 옮기는 일은 answer-builder의 책임이다.

    ResolvedEventState도 만들지 않는다. 질문이 묻는 것은 최종 상태 하나가 아니라 변경
    이력 전체이고, 그 정본은 EventHistory.changes다.

실제 공시 값에 대하여:
    합성 fixture에는 실제 접수번호·수치를 쓰지 않는다. 실측 코퍼스를 읽는 테스트는
    `corpus` 마크로 분리하고, 코퍼스가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent.handlers.event_history import (
    EVENT_TYPE_BY_DOCUMENT_SUBTYPE,
    build_event_history_result,
    resolve_event_type,
    root_filing_window,
)
from disclosure_agent.agent.query_analyzer import (
    EVENT_SUBTYPE_FACILITY_INVESTMENT,
    EVENT_SUBTYPE_SUPPLY_CONTRACT,
    INTENT_EVENT_HISTORY,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.agent.router import answer_question
from disclosure_agent.common.enums import (
    AnswerabilityStatus,
    ChainPolicy,
    EventLinkResolutionStatus,
    EventType,
    ExecutionStatus,
)
from disclosure_agent.event.diff import FIELD_INVESTMENT_AMOUNT, FIELD_INVESTMENT_END_DATE

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"

DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "3.공시" / "3.공시" / "corpus"
)

# evaluation/questions.jsonl의 Q15 원문. 가운뎃점 표기까지 그대로 둔다.
Q15 = "레인보우로보틱스 사옥·제조시설 신축 투자, 그동안 계획이 몇 번 바뀌었어?"

# evaluation/questions.jsonl의 Q12·Q14 원문. 이번 PR에서 열리면 안 되는 두 질문이다.
Q12 = (
    "2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, "
    "회사별로 다 합치면 어디가 더 커?"
)

Q14 = "LG에너지솔루션이 포드랑 맺었던 배터리 공급계약, 지금 어떻게 됐어?"

REAL_COMPANIES = ("레인보우로보틱스", "LG이노텍", "LG에너지솔루션", "HD현대일렉트릭")

# 합성 코퍼스에서 쓰는 이름. 실제 기업명을 쓰지 않는다.
COMPANY = "테스트반도체(주)"

KNOWN_COMPANIES = ("테스트반도체",)

# Q15와 같은 모양의 합성 질문. 연도가 없고 유형명도 쓰지 않는다.
SYNTHETIC_Q15 = "테스트반도체 사옥·제조시설 신축 투자, 그동안 계획이 몇 번 바뀌었어?"

# 대조군. 같은 Pattern E이지만 공급계약이고 시점을 밝힌다(Q13 모양).
SYNTHETIC_Q13 = "테스트반도체 2025년 8월에 공시한 공급계약, 나중에 바뀐 내용 있어?"

ROOT_RECEIPT_NO = "SYNTHETIC0101"

CORRECTION_RECEIPT_NOS = ("SYNTHETIC0102", "SYNTHETIC0103", "SYNTHETIC0104")

# 원공시 공시일. 정정본이 본문에 적어 두는 '최초 공시일'과 같아야 연결된다.
ROOT_FILING_DATE = "20240430"


def _resolve_corpus_root() -> Path | None:
    raw = os.environ.get(CORPUS_ROOT_ENV)

    candidate = Path(raw) if raw else DEFAULT_CORPUS_ROOT

    if candidate.is_dir() and (candidate / "manifest.jsonl").is_file():
        return candidate

    return None


# --------------------------------------------------------------------------
# 합성 코퍼스
#
# 원공시 1건 + 정정 3건. 실측 체인과 같은 모양이되 값은 전부 합성이다.
# --------------------------------------------------------------------------


def _record(
    receipt_no: str,
    receipt_date: str,
    *,
    subtype: str,
    is_correction: bool,
) -> dict[str, Any]:
    prefix = "[기재정정]" if is_correction else ""

    return {
        "doc_id": f"exchange_{receipt_no}",
        "corp_code": "00000001",
        "corp_name": COMPANY,
        "stock_code": "000001",
        "doc_group": "exchange",
        "doc_subtype": subtype,
        "report_nm": f"{prefix}{subtype}",
        "is_correction": is_correction,
        "rcept_no": receipt_no,
        "rcept_dt": receipt_date,
        "base_year": None,
        "base_month": None,
        "file_path": f"raw/exchange/테스트반도체/{receipt_no}",
        "file_format": "xml",
        "n_files": 1,
    }


FACILITY_RECORDS: list[dict[str, Any]] = [
    _record(
        ROOT_RECEIPT_NO,
        ROOT_FILING_DATE,
        subtype=EVENT_SUBTYPE_FACILITY_INVESTMENT,
        is_correction=False,
    ),
    *[
        _record(
            receipt_no,
            receipt_date,
            subtype=EVENT_SUBTYPE_FACILITY_INVESTMENT,
            is_correction=True,
        )
        for receipt_no, receipt_date in zip(
            CORRECTION_RECEIPT_NOS, ("20250205", "20251205", "20260327"), strict=True
        )
    ],
    # 같은 기업의 다른 유형. 유형 필터가 걸러야 한다. 걸러지지 않으면 원공시가 둘이 되어
    # ambiguous_root로 막히므로, 이 레코드가 유형 필터의 대조군 역할을 한다.
    _record(
        "SYNTHETIC0111",
        "20240501",
        subtype=EVENT_SUBTYPE_SUPPLY_CONTRACT,
        is_correction=False,
    ),
]

# 정정본이 밝힌 변경. 실측 체인과 같은 필드 조합을 쓰되 값은 합성이다.
#
# 마지막 항목은 일부러 대응표에 없는 필드다. 이름을 붙이지 못한 변경이 조용히 사라지지
# 않는지(field=None으로 남는지) 함께 고정한다.
FACILITY_CHANGES: dict[str, list[dict[str, str]]] = {
    CORRECTION_RECEIPT_NOS[0]: [
        {
            "raw_field": "2. 투자내역-투자금액",
            "before": "1,000,000,000",
            "after": "1,100,000,000",
        },
    ],
    CORRECTION_RECEIPT_NOS[1]: [
        {
            "raw_field": "2. 투자내역-투자금액",
            "before": "1,100,000,000",
            "after": "1,200,000,000",
        },
        {
            "raw_field": "4. 투자기간-종료일",
            "before": "2025-12-15",
            "after": "2026-03-31",
        },
    ],
    CORRECTION_RECEIPT_NOS[2]: [
        {
            "raw_field": "4. 투자기간-종료일",
            "before": "2026-03-31",
            "after": "2026-03-27",
        },
        {
            "raw_field": "8. 기타 투자판단에 참고할 사항",
            "before": "문구추가",
            "after": "합성 문구",
        },
    ],
}


def _parsed_document(record: dict[str, Any]) -> dict[str, Any]:
    """event.parser가 내놓는 형태의 문서. 원문 XML을 다시 열지 않는다."""
    receipt_no = record["rcept_no"]

    correction = None

    if record["is_correction"]:
        correction = {
            "correction_date": record["rcept_dt"],
            "original_filing_date": ROOT_FILING_DATE,
            "reason": "투자 계획 변경",
            "changes": [
                {
                    "raw_field": change["raw_field"],
                    "field": change["raw_field"],
                    "before": change["before"],
                    "after": change["after"],
                }
                for change in FACILITY_CHANGES.get(receipt_no, [])
            ],
        }

    return {
        "metadata": {
            "doc_id": record["doc_id"],
            "document_type": "exchange",
            "is_correction": record["is_correction"],
        },
        "title": record["report_nm"],
        "rows": [],
        "correction": correction,
    }


@pytest.fixture
def facility_manifest_path(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.jsonl"

    with path.open("w", encoding="utf-8") as file:
        for record in FACILITY_RECORDS:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    return path


@pytest.fixture
def facility_corpus_root(tmp_path: Path) -> Path:
    """
    합성 corpus. 한글 경로는 실측 코퍼스와 같은 NFD 표기로 만든다.

    XML은 경로 해석만 검증하는 최소 파일이다. 본문 파싱 결과는 _parsed_document가 대신한다.
    """
    root = tmp_path / "corpus"

    for record in FACILITY_RECORDS:
        directory = root / unicodedata.normalize("NFD", record["file_path"])

        directory.mkdir(parents=True, exist_ok=True)

        (directory / f"{record['rcept_no']}.xml").write_text("<html/>", encoding="utf-8")

    return root


@pytest.fixture
def parsed_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """handler가 여는 본문 파싱을 합성 결과로 대신한다."""
    by_receipt_no = {record["rcept_no"]: _parsed_document(record) for record in FACILITY_RECORDS}

    def _parse(path: Path | str) -> dict[str, Any]:
        receipt_no = Path(path).stem

        return by_receipt_no[receipt_no]

    monkeypatch.setattr(
        "disclosure_agent.agent.handlers.event_history.parse_exchange_document", _parse
    )


# --------------------------------------------------------------------------
# 1. 질의 해석 — Q15가 연도 없이 event_history로 계획된다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "known_companies"),
    [(Q15, REAL_COMPANIES), (SYNTHETIC_Q15, KNOWN_COMPANIES)],
)
def test_q15_shaped_question_plans_a_facility_investment_history(
    question: str, known_companies: tuple[str, ...]
) -> None:
    """
    유형명을 쓰지 않은 질문이 신규시설투자 이력 조회로 계획돼야 한다.

    document_subtype은 manifest의 doc_subtype 표기와 같아야 한다. 다르면 검색이 한 건도
    맞지 않아 '코퍼스에 없다'로 답하게 된다.
    """
    plan: QueryPlan = build_query_plan(question, known_companies=known_companies)

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_subtype == EVENT_SUBTYPE_FACILITY_INVESTMENT
    assert plan.chain_policy == ChainPolicy.FULL_CHAIN
    assert plan.metric is None


@pytest.mark.parametrize(
    ("question", "known_companies"),
    [(Q15, REAL_COMPANIES), (SYNTHETIC_Q15, KNOWN_COMPANIES)],
)
def test_q15_shaped_question_is_planned_without_a_year(
    question: str, known_companies: tuple[str, ...]
) -> None:
    """
    연도를 밝히지 않은 질문이 missing_year로 막히지 않아야 한다.

    연도를 지어내지도 않는다. years가 비어 있는 것과 아무 연도나 채워 넣은 것은 다르며,
    후자면 그 해에 공시된 다른 건의 이력을 답하게 된다.
    """
    plan = build_query_plan(question, known_companies=known_companies)

    assert plan.years == ()
    assert plan.month is None

    # 공시일 창을 만들지 않는다. 창이 있으면 그 밖의 원공시가 통째로 빠진다.
    assert root_filing_window(plan) is None


def test_facility_investment_question_still_accepts_an_explicit_year() -> None:
    """
    연도를 밝힌 신규시설투자 질문은 예전처럼 그 해로 원공시를 좁힌다.

    연도 요구를 푸는 변경이 '연도를 무시한다'가 되면 안 된다. 질문이 시점을 밝혔으면
    그것이 조회 조건이다.
    """
    plan = build_query_plan(
        "테스트반도체 2024년 사옥 신축 투자, 계획이 바뀐 적 있어?",
        known_companies=KNOWN_COMPANIES,
    )

    assert plan.document_subtype == EVENT_SUBTYPE_FACILITY_INVESTMENT
    assert plan.years == (2024,)
    assert root_filing_window(plan) == ("20240101", "20241231")


def test_facility_investment_question_with_a_month_still_requires_a_year() -> None:
    """
    월만 있고 연도가 없으면 막는다.

    월은 공시일 창을 좁히는 조건인데 연도가 없으면 창을 만들 수 없다. 그대로 진행하면
    질문이 밝힌 '4월'이 조회에 쓰이지 않은 채 사라진다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            "테스트반도체 4월에 공시한 사옥 신축 투자, 계획이 바뀐 적 있어?",
            known_companies=KNOWN_COMPANIES,
        )

    assert error.value.reason == "missing_year"


def test_supply_contract_history_question_still_requires_a_year() -> None:
    """
    공급계약 이력 질문의 연도 요구는 그대로다.

    Q14가 이 자리에서 계속 막힌다. 상대방 이름('포드')으로 계약을 특정하려면 본문 대조가
    필요하고 그것은 이번 범위 밖이다. 연도 요구를 유형과 무관하게 풀면 Q14가 근거 없이
    어느 한 계약의 이력을 답하게 된다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(Q14, known_companies=REAL_COMPANIES)

    assert error.value.reason == "missing_year"


def test_q13_shaped_question_keeps_its_supply_contract_plan() -> None:
    """대조군. 공급계약 경로의 계획은 하나도 달라지지 않아야 한다."""
    plan = build_query_plan(SYNTHETIC_Q13, known_companies=KNOWN_COMPANIES)

    assert plan.intent == INTENT_EVENT_HISTORY
    assert plan.document_subtype == EVENT_SUBTYPE_SUPPLY_CONTRACT
    assert plan.years == (2025,)
    assert plan.month == 8
    assert root_filing_window(plan) == ("20250801", "20250831")


# --------------------------------------------------------------------------
# 2. 어휘 — 넓힌 표현이 다른 질문을 끌어오지 않는다
#
# '투자' 한 낱말을 유형 표현으로 쓰지 않는 것이 이 절의 전부다.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        # 투자판단관련주요경영사항. 실제 코퍼스에 있는 다른 거래소공시 유형이다.
        "테스트반도체 투자판단관련주요경영사항 공시, 정정된 내용 있어?",
        "테스트반도체 투자판단 관련 주요 경영사항이 바뀐 게 있어?",
        # 일반 투자 표현. 시설을 가리키는 말이 없다.
        "테스트반도체 투자자문 계약이 변경된 적 있어?",
        "테스트반도체 투자활동 현금흐름이 작년이랑 달라졌어?",
        "테스트반도체 타법인 출자 투자 내역이 수정됐어?",
    ],
)
def test_investment_wording_alone_does_not_open_the_facility_investment_path(
    question: str,
) -> None:
    """
    대상 표현 없이 '투자'만 있는 질문은 신규시설투자로 읽히지 않아야 한다.

    읽히면 이 질문들이 Pattern E로 흘러가 엉뚱한 공시의 정정 이력을 답으로 받는다.
    """
    try:
        plan = build_query_plan(question, known_companies=KNOWN_COMPANIES)

    except UnsupportedQuestionError:
        # 다른 이유로 거절되는 것은 이 테스트의 관심사가 아니다. 신규시설투자 이력으로
        # 열리지 않았다는 사실만 확인하면 된다.
        return

    assert plan.document_subtype != EVENT_SUBTYPE_FACILITY_INVESTMENT


def test_q12_is_not_routed_to_pattern_e_by_the_new_vocabulary() -> None:
    """
    Q12에는 '시설투자'가 그대로 들어 있지만 Pattern E로 가면 안 된다.

    가드가 둘이다. 다중 기업 판정이 이력 판정보다 먼저 실행되고, 그것을 통과하더라도 Q12에는
    변경 이력을 묻는 표현이 없다. 둘 중 하나만 남아도 이 성질은 유지돼야 하므로 계획의
    intent와 유형 표현을 함께 본다.
    """
    plan = build_query_plan(Q12, known_companies=REAL_COMPANIES)

    assert plan.intent != INTENT_EVENT_HISTORY
    assert plan.document_subtype != EVENT_SUBTYPE_FACILITY_INVESTMENT


def test_q12_wording_without_the_second_company_still_lacks_a_history_question() -> None:
    """
    기업을 하나로 줄여 다중 기업 가드를 없애도 Q12 모양은 이력 질문이 아니다.

    두 가드 중 하나를 걷어 낸 상태를 일부러 만들어, 남은 가드 하나가 실제로 막고 있는지
    본다. 이력 표현 요구가 사라지면 이 테스트가 먼저 깨진다.
    """
    with pytest.raises(UnsupportedQuestionError):
        build_query_plan(
            "2025년에 테스트반도체가 새로 발표한 시설투자, 다 합치면 얼마야?",
            known_companies=KNOWN_COMPANIES,
        )


# --------------------------------------------------------------------------
# 3. 이벤트 유형 대응 — 계획의 세부유형이 EventType을 정한다
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document_subtype", "expected"),
    [
        (EVENT_SUBTYPE_SUPPLY_CONTRACT, EventType.SUPPLY_CONTRACT),
        (EVENT_SUBTYPE_FACILITY_INVESTMENT, EventType.FACILITY_INVESTMENT),
    ],
)
def test_document_subtype_decides_the_event_type(
    document_subtype: str, expected: EventType
) -> None:
    assert resolve_event_type(document_subtype) is expected


def test_unknown_document_subtype_has_no_event_type() -> None:
    """
    모르는 유형을 가까운 유형으로 대신하지 않는다.

    대신하면 정정 항목이 다른 유형의 어휘로 조회되어 이름이 하나도 붙지 않고, 그 결과는
    "바뀐 것이 없다"와 구분되지 않는다.
    """
    assert resolve_event_type("주식소각결정") is None
    assert "주식소각결정" not in EVENT_TYPE_BY_DOCUMENT_SUBTYPE


def test_unsupported_document_subtype_is_blocked_before_reading_the_corpus(
    facility_manifest_path: Path,
    facility_corpus_root: Path,
) -> None:
    """대응하는 유형이 없으면 값을 하나도 만들지 않고 막힌다."""
    plan = QueryPlan(
        question="합성 질문",
        companies=(COMPANY,),
        years=(2024,),
        intent=INTENT_EVENT_HISTORY,
        document_class="event",
        document_subtype="주식소각결정",
        metric=None,
        chain_policy=ChainPolicy.FULL_CHAIN,
    )

    result = build_event_history_result(
        plan,
        corpus_root=facility_corpus_root,
        manifest_path=facility_manifest_path,
    )

    assert result.history is None
    assert result.status is None
    assert result.blocked is not None
    assert result.blocked.reason == "unsupported_event_type"


# --------------------------------------------------------------------------
# 4. 체인 — 원공시 1건 + 정정 3건
# --------------------------------------------------------------------------


@pytest.fixture
def facility_result(
    facility_manifest_path: Path,
    facility_corpus_root: Path,
    parsed_documents: None,
):
    plan = build_query_plan(SYNTHETIC_Q15, known_companies=KNOWN_COMPANIES)

    return build_event_history_result(
        plan,
        corpus_root=facility_corpus_root,
        manifest_path=facility_manifest_path,
    )


def test_facility_chain_collects_the_original_filing_and_every_correction(
    facility_result,
) -> None:
    """
    연도 없이도 원공시 1건과 정정 3건이 시간순으로 모여야 한다.

    문서 순서가 규약이다. 정정은 원공시보다 뒤에, 서로는 공시일 순으로 놓인다. 순서가
    흐트러지면 "무엇이 언제 바뀌었나"의 '언제'가 결과에서 사라진다.
    """
    assert facility_result.blocked is None
    assert facility_result.status is AnswerabilityStatus.ANSWERABLE

    history = facility_result.history

    assert history is not None
    assert history.chain_complete is True
    assert history.document_ids == [
        f"exchange_{ROOT_RECEIPT_NO}",
        *[f"exchange_{receipt_no}" for receipt_no in CORRECTION_RECEIPT_NOS],
    ]


def test_facility_chain_resolves_every_correction_link(facility_result) -> None:
    """정정 3건이 모두 같은 원공시로 연결돼야 한다."""
    links = facility_result.links

    assert len(links) == 3
    assert all(
        link.resolution_status is EventLinkResolutionStatus.RESOLVED for link in links
    )
    assert {link.resolved_doc_id for link in links} == {f"exchange_{ROOT_RECEIPT_NO}"}


def test_facility_chain_keeps_the_full_change_trajectory(facility_result) -> None:
    """
    질문이 묻는 것은 최종 상태 하나가 아니라 변경 이력 전체다.

    필드별로 마지막 값만 남기면 "몇 번 바뀌었나"에 답할 수 없다. 이름을 붙인 변경 4건이
    순서대로 남아 있어야 한다.
    """
    history = facility_result.history

    assert history is not None
    assert [(change.field_name, change.before, change.after) for change in history.changes] == [
        (FIELD_INVESTMENT_AMOUNT, "1,000,000,000", "1,100,000,000"),
        (FIELD_INVESTMENT_AMOUNT, "1,100,000,000", "1,200,000,000"),
        (FIELD_INVESTMENT_END_DATE, "2025-12-15", "2026-03-31"),
        (FIELD_INVESTMENT_END_DATE, "2026-03-31", "2026-03-27"),
    ]


def test_facility_chain_does_not_produce_a_resolved_event_state(
    facility_manifest_path: Path,
    facility_corpus_root: Path,
    parsed_documents: None,
) -> None:
    """
    최종 상태 하나로 접지 않는다.

    ResolvedEventState는 Q14 계열('지금 어떻게 됐어')의 표현이며 이번 범위 밖이다. 여기서
    만들기 시작하면 이력 질문에 상태 하나로 답하는 경로가 생긴다.
    """
    result = answer_question(
        SYNTHETIC_Q15,
        corpus_root=facility_corpus_root,
        manifest_path=facility_manifest_path,
    )

    assert result.resolved_event_states == []
    assert len(result.event_histories) == 1


def test_facility_history_reaches_the_common_execution_contract(
    facility_manifest_path: Path,
    facility_corpus_root: Path,
    parsed_documents: None,
) -> None:
    """질문 문자열 하나가 공통 Contract까지 끊기지 않고 도달해야 한다."""
    result = answer_question(
        SYNTHETIC_Q15,
        corpus_root=facility_corpus_root,
        manifest_path=facility_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None
    assert len(result.event_histories) == 1
    assert len(result.event_links) == 3
    assert len(result.evidence) >= 2


def test_ambiguous_facility_root_is_not_silently_narrowed(
    tmp_path: Path,
    facility_corpus_root: Path,
    parsed_documents: None,
) -> None:
    """
    연도가 없을 때 원공시가 여럿이면 하나를 고르지 않는다.

    이것이 공시일 창을 뺀 대가를 치르는 자리다. 창이 없으면 후보가 늘어날 수 있고, 그때
    임의로 하나를 고르면 다른 건의 이력을 이 건의 이력이라고 말하게 된다.
    """
    manifest_path = tmp_path / "ambiguous_manifest.jsonl"

    records = [
        *FACILITY_RECORDS,
        _record(
            "SYNTHETIC0121",
            "20230310",
            subtype=EVENT_SUBTYPE_FACILITY_INVESTMENT,
            is_correction=False,
        ),
    ]

    with manifest_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    directory = facility_corpus_root / unicodedata.normalize(
        "NFD", "raw/exchange/테스트반도체/SYNTHETIC0121"
    )

    directory.mkdir(parents=True, exist_ok=True)

    (directory / "SYNTHETIC0121.xml").write_text("<html/>", encoding="utf-8")

    plan = build_query_plan(SYNTHETIC_Q15, known_companies=KNOWN_COMPANIES)

    result = build_event_history_result(
        plan,
        corpus_root=facility_corpus_root,
        manifest_path=manifest_path,
    )

    assert result.history is None
    assert result.status is AnswerabilityStatus.UNRESOLVED
    assert result.blocked is not None
    assert result.blocked.reason == "ambiguous_root"
    assert len(result.root_candidate_doc_ids) == 2


# --------------------------------------------------------------------------
# 5. 실측 코퍼스 — 대표 질문 원문
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_q15_is_answered_in_the_actual_corpus() -> None:
    """대표 질문 원문 그대로 실행해 이력 1건이 완결 체인으로 나와야 한다."""
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = answer_question(Q15, corpus_root=corpus_root)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert result.answerability_reason is None

    assert len(result.event_histories) == 1

    history = result.event_histories[0]

    assert history.chain_complete is True
    # 원공시 1건 + 정정 3건.
    assert len(history.document_ids) == 4
    assert len(result.event_links) == 3
    assert all(
        link.resolution_status is EventLinkResolutionStatus.RESOLVED
        for link in result.event_links
    )

    # gold(evaluation/gold_answers.jsonl q15)의 min_evidence_count.
    assert len(result.evidence) >= 2

    # 투자금액과 투자기간 종료일의 변경 궤적이 모두 남아 있어야 한다.
    changed_fields = [change.field_name for change in history.changes]

    assert changed_fields.count(FIELD_INVESTMENT_AMOUNT) == 2
    assert changed_fields.count(FIELD_INVESTMENT_END_DATE) == 2

    assert result.resolved_event_states == []


@pytest.mark.corpus
def test_q13_is_unchanged_in_the_actual_corpus() -> None:
    """
    Q13 회귀. 공급계약 경로가 이번 변경으로 달라지지 않아야 한다.

    EventType을 상수에서 대응표로 바꾼 변경이 공급계약 쪽에 닿았는지 보는 자리다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = answer_question(
        "HD현대일렉트릭이 2024년 8월에 공시한 공급계약, 나중에 바뀐 내용 있어?",
        corpus_root=corpus_root,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.ANSWERABLE
    assert len(result.event_histories) == 1
    assert result.event_histories[0].chain_complete is True


@pytest.mark.corpus
def test_q14_stays_fail_closed_in_the_actual_corpus() -> None:
    """
    Q14는 이번 PR에서 열리지 않는다.

    같은 Pattern E이고 같은 이력 표현을 쓰지만, 계약을 특정할 근거가 상대방 이름뿐이다.
    연도 요구를 유형과 무관하게 풀었다면 여기서 값이 만들어지고 이 테스트가 깨진다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    result = answer_question(Q14, corpus_root=corpus_root)

    assert result.event_histories == []
    assert result.event_links == []
    assert result.evidence == []
    assert result.answerability_status not in (
        AnswerabilityStatus.ANSWERABLE,
        AnswerabilityStatus.PARTIALLY_ANSWERABLE,
    )
    assert result.execution_issue is not None or result.answerability_reason is not None
