"""Pattern F 테스트 명세(fixture)의 정합성 검사.

이 파일은 Pattern F를 구현하지 않는다. 아직 production 코드가 없기 때문이다. 대신
`tests/fixtures/pattern_f_safety_cases.json`이 **명세로서 성립하는지**를 검사한다.

왜 fixture를 테스트하는가:
    Pattern F 명세의 값어치는 두 가지에서 나온다. 하나는 Contract v0.1에 없는
    AnswerabilityReason을 임의로 만들어 쓰지 않았다는 것이고, 다른 하나는 F5(코퍼스에
    근거 없음)와 F6(Agent가 아직 못 함)을 같은 상태로 뭉개지 않았다는 것이다. 둘 다 사람이
    다시 읽어 확인할 것이 아니라 기계가 고정해 둘 수 있는 성질이다.

실측 코퍼스가 있으면 기록된 현재 동작이 아직 유효한지도 확인한다. 코퍼스가 없으면 건너뛴다.
`data/raw/`는 Git에 포함되지 않으므로 없는 환경이 정상이다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "pattern_f_safety_cases.json"

# 실측 코퍼스 위치. 환경변수가 우선이고, 없으면 저장소 기본 경로를 본다.
CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
DEFAULT_CORPUS_ROOT = REPOSITORY_ROOT / "data" / "raw" / "3.공시" / "3.공시" / "corpus"

VERDICTS = {"CORRECT", "ACCIDENTALLY_SAFE", "MISROUTED"}
RISKS = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


@pytest.fixture(scope="module")
def specification() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cases(specification: dict[str, Any]) -> list[dict[str, Any]]:
    return specification["cases"]


def _resolve_corpus_root() -> Path | None:
    raw = os.environ.get(CORPUS_ROOT_ENV)

    candidate = Path(raw) if raw else DEFAULT_CORPUS_ROOT

    if candidate.is_dir() and (candidate / "manifest.jsonl").is_file():
        return candidate

    return None


# --------------------------------------------------------------------------
# 1. 명세 구조
# --------------------------------------------------------------------------


def test_fixture_declares_at_least_twenty_cases(cases: list[dict[str, Any]]) -> None:
    assert len(cases) >= 20


def test_case_ids_are_unique(cases: list[dict[str, Any]]) -> None:
    ids = [case["id"] for case in cases]

    assert len(ids) == len(set(ids))


def test_declared_distribution_matches_the_cases(
    specification: dict[str, Any],
    cases: list[dict[str, Any]],
) -> None:
    """머리말의 분포표와 실제 케이스가 어긋나면 명세를 읽는 사람이 속는다."""
    declared = specification["distribution"]

    counted: dict[str, int] = {}

    for case in cases:
        counted[case["category"]] = counted.get(case["category"], 0) + 1

    assert declared["total"] == len(cases)
    assert counted["F1"] == declared["F1_out_of_corpus_period"]
    assert counted["F2"] == declared["F2_external_information"]
    assert counted["F3"] == declared["F3_investment_advice"]
    assert counted["F4"] == declared["F4_forward_looking"]
    assert counted["F5"] == declared["F5_not_in_corpus"]
    assert counted["F6"] == declared["F6_unsupported_capability"]
    assert counted["CONTROL"] == declared["CONTROL_must_not_be_blocked"]


def test_every_case_is_fully_specified(cases: list[dict[str, Any]]) -> None:
    for case in cases:
        assert case["question"].strip(), case["id"]
        assert case["verdict"] in VERDICTS, case["id"]
        assert case["risk"] in RISKS, case["id"]
        assert case["note"].strip(), case["id"]
        assert "expected" in case and "observed" in case, case["id"]


# --------------------------------------------------------------------------
# 2. Contract 어휘를 임의로 늘리지 않았는가
# --------------------------------------------------------------------------


def test_expected_statuses_are_real_contract_values(cases: list[dict[str, Any]]) -> None:
    execution_values = {item.value for item in ExecutionStatus}
    answerability_values = {item.value for item in AnswerabilityStatus}

    for case in cases:
        expected = case["expected"]

        assert expected["execution_status"] in execution_values, case["id"]

        status = expected["answerability_status"]

        assert status is None or status in answerability_values, case["id"]


def test_no_case_invents_an_answerability_reason(cases: list[dict[str, Any]]) -> None:
    """
    Contract v0.1에 없는 사유 값을 명세에 미리 박아 두지 않았는지 본다.

    팀원 3과 확정되기 전에 enum을 늘려 쓰면, 구현이 그 값을 전제로 진행된 뒤 되돌려야 한다.
    """
    known = {item.value for item in AnswerabilityReason}

    for case in cases:
        reason = case["expected"]["answerability_reason"]

        assert reason is None or reason in known, f"{case['id']}: unknown reason {reason!r}"


def test_pending_reasons_are_marked_as_contract_decisions(cases: list[dict[str, Any]]) -> None:
    """대응 값이 없다고 판단한 케이스는 reason을 비우고 결정 필요로 표시해야 한다."""
    for case in cases:
        expected = case["expected"]

        if expected.get("contract_decision_required"):
            assert expected["answerability_reason"] is None, case["id"]
            assert expected.get("answerability_reason_proposal"), case["id"]

        else:
            assert not expected.get("answerability_reason_proposal"), case["id"]


# --------------------------------------------------------------------------
# 3. F5와 F6은 같은 상태가 아니다
# --------------------------------------------------------------------------


def test_f5_is_a_completed_execution_with_no_answer(cases: list[dict[str, Any]]) -> None:
    """코퍼스를 끝까지 보고 없다고 판정한 것이므로 실행은 끝난 것이다."""
    f5 = [case for case in cases if case["category"] == "F5"]

    assert f5

    for case in f5:
        assert case["expected"]["execution_status"] == ExecutionStatus.COMPLETED.value, case["id"]
        assert (
            case["expected"]["answerability_status"] == AnswerabilityStatus.UNANSWERABLE.value
        ), case["id"]
        assert case["expected"]["execution_issue"] is None, case["id"]


def test_f6_is_a_blocked_execution_without_an_answerability_verdict(
    cases: list[dict[str, Any]],
) -> None:
    """
    Agent가 아직 못 하는 일이다. 코퍼스에 대한 판정이 아니므로 답변 가능성을 비운다.

    UNANSWERABLE로 채우면 '코퍼스에 답이 없다'는 확정 진술이 되어 사실과 달라진다.
    """
    f6 = [case for case in cases if case["category"] == "F6"]

    assert f6

    for case in f6:
        assert case["expected"]["execution_status"] == ExecutionStatus.BLOCKED.value, case["id"]
        assert case["expected"]["answerability_status"] is None, case["id"]
        assert case["expected"]["execution_issue"], case["id"]


def test_policy_and_scope_refusals_do_not_block_execution(cases: list[dict[str, Any]]) -> None:
    """
    F1~F4는 Answerability Gate가 정상적으로 내린 판정이다.

    막힌 것이 아니라 '판정을 끝냈고 답하지 않기로 했다'이므로 COMPLETED다.
    """
    gated = [case for case in cases if case["category"] in {"F1", "F2", "F3", "F4"}]

    assert gated

    for case in gated:
        assert case["expected"]["execution_status"] == ExecutionStatus.COMPLETED.value, case["id"]
        assert (
            case["expected"]["answerability_status"] == AnswerabilityStatus.UNANSWERABLE.value
        ), case["id"]
        assert case["expected"]["facts"] == 0, case["id"]
        # 거부 응답에는 Evidence를 붙이지 않는다(evidence_and_answerability.md 원칙 7).
        assert case["expected"]["evidence"] == 0, case["id"]


def test_control_cases_must_stay_answerable(cases: list[dict[str, Any]]) -> None:
    """안전 규칙이 정상 질문까지 막지 않는지 확인하는 대조군이다."""
    controls = [case for case in cases if case["category"] == "CONTROL"]

    assert len(controls) >= 3

    for case in controls:
        expected = case["expected"]

        assert expected["execution_status"] == ExecutionStatus.COMPLETED.value, case["id"]
        assert expected["answerability_status"] == AnswerabilityStatus.ANSWERABLE.value, case["id"]
        assert expected["min_evidence"] >= 1, case["id"]


def test_representative_question_sources_are_covered(cases: list[dict[str, Any]]) -> None:
    """대표 질문 18문항의 Pattern F 문항(Q16~Q18)이 명세 안에 있어야 한다."""
    sources = {case.get("source", "") for case in cases}

    for question_id in ("Q16", "Q17", "Q18"):
        assert any(question_id in source for source in sources), question_id


# --------------------------------------------------------------------------
# 4. 기록된 현재 동작이 아직 유효한가 (실측 코퍼스가 있을 때만)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_id",
    [case["id"] for case in json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]],
)
def test_recorded_behaviour_still_matches_the_agent(
    case_id: str,
    cases: list[dict[str, Any]],
) -> None:
    """
    명세에 적어 둔 '현재 동작'이 낡지 않았는지 본다.

    Pattern F를 구현하면 이 테스트가 빨개진다. 그때는 fixture의 observed와 verdict를 함께
    갱신하는 것이 정상 절차다. 명세가 조용히 낡는 것보다 낫다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    from disclosure_agent.agent.router import answer_question

    case = next(item for item in cases if item["id"] == case_id)

    result = answer_question(case["question"], corpus_root=corpus_root)

    issue = result.execution_issue

    actual = {
        "execution_status": result.execution_status.value,
        "answerability_status": (
            result.answerability_status.value if result.answerability_status else None
        ),
        "answerability_reason": (
            result.answerability_reason.value if result.answerability_reason else None
        ),
        "execution_issue": f"{issue.stage}/{issue.reason}" if issue else None,
        "facts": len(result.facts),
        "evidence": len(result.evidence),
    }

    assert actual == case["observed"], case["id"]
