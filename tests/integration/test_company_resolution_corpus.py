"""실제 코퍼스 manifest로 확인하는 기업명 해석 회귀.

단위 테스트와 나누는 이유:
    tests/unit/test_company_resolution.py는 합성 목록으로 규칙을 고정한다. 규칙이 옳아도
    manifest가 상장명을 싣지 않거나 필드 이름이 다르면 실제 질문은 여전히 막힌다. 여기서
    보는 것은 그 연결이며, 규칙 자체는 다시 시험하지 않는다.

해석기까지만 보는 자리와 공개 경계까지 보는 자리:
    앞쪽은 manifest -> 해석기 연결만 본다. 문서를 열지 않는데, 기업명 해석은 manifest만으로
    끝나고 여기서 문서를 열면 파싱 실패가 기업명 해석 실패처럼 보이기 때문이다.

    뒤쪽(공개 경계 회귀)은 answer_question 한 번을 실제로 지나간다. 해석기가 옳아도 그 결과가
    Router를 지나며 canonical로 남지 않으면 실제 질문은 여전히 다른 회사를 답하거나 막힌다.
    그 연결은 해석기만 불러서는 드러나지 않는다.

코퍼스가 없으면 건너뛴다. 저장소에 실제 공시 파일을 두지 않기 때문이다(AGENTS.md).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.company_resolution import (
    RESOLUTION_ALIAS,
    RESOLUTION_LISTED_NAME,
    RESOLUTION_UNIQUE_PARTIAL,
)
from disclosure_agent.agent.query_analyzer import (
    AMBIGUOUS_COMPANY_REASON,
    build_company_resolver,
)
from disclosure_agent.common.enums import ExecutionStatus
from disclosure_agent.retrieval.metadata_filter import (
    list_company_names,
    list_company_universe,
    load_manifest,
    normalize_company_name,
)

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"


def _corpus_root() -> Path:
    raw_corpus_root = os.environ.get(CORPUS_ROOT_ENV)

    if not raw_corpus_root:
        pytest.skip(f"{CORPUS_ROOT_ENV} is not set")

    corpus_root = Path(raw_corpus_root).expanduser()

    if not corpus_root.is_dir():
        pytest.skip(f"corpus root is not a directory: {corpus_root}")

    return corpus_root


def _manifest_path() -> Path:
    corpus_root = _corpus_root()

    raw_manifest = os.environ.get(CORPUS_MANIFEST_ENV)

    manifest_path = (
        Path(raw_manifest).expanduser()
        if raw_manifest is not None
        else corpus_root / "manifest.jsonl"
    )

    if not manifest_path.is_file():
        pytest.skip(f"corpus manifest does not exist: {manifest_path}")

    return manifest_path


@pytest.fixture
def resolver():
    records = load_manifest(_manifest_path())

    return build_company_resolver(
        list_company_names(records),
        company_universe=list_company_universe(records),
    )


@pytest.mark.corpus
def test_manifest_carries_listed_names() -> None:
    """
    상장명이 manifest에 실제로 있는지 먼저 확인한다.

    이 확인이 없으면 아래 테스트가 '상장명이 없어서' 실패했는지 '해석 규칙이 틀려서' 실패했는지
    구분되지 않는다.
    """
    universe = list_company_universe(load_manifest(_manifest_path()))

    assert universe

    assert any(row["listed_name"] != row["corp_name"] for row in universe)


@pytest.mark.corpus
@pytest.mark.parametrize(
    ("mention", "expected", "method"),
    [
        ("현대차", "현대자동차", RESOLUTION_LISTED_NAME),
        ("삼성화재", "삼성화재해상보험", RESOLUTION_LISTED_NAME),
        ("KT", "케이티", RESOLUTION_LISTED_NAME),
        ("삼바", "삼성바이오로직스", RESOLUTION_ALIAS),
        ("기아자동차", "기아", RESOLUTION_ALIAS),
        ("레인보우", "레인보우로보틱스", RESOLUTION_UNIQUE_PARTIAL),
    ],
)
def test_common_spellings_resolve_against_the_actual_corpus(
    resolver,
    mention: str,
    expected: str,
    method: str,
) -> None:
    resolution = resolver.resolve_question(f"{mention} 2025년 연결 기준 매출액이 얼마야?")

    assert resolution.companies == (expected,)

    assert resolution.mentions[0].method == method


@pytest.mark.corpus
@pytest.mark.parametrize("mention", ["삼성", "현대", "한화", "두산", "한미"])
def test_group_prefixes_stay_ambiguous_against_the_actual_corpus(
    resolver,
    mention: str,
) -> None:
    """
    계열사 접두어는 실제 코퍼스에서도 확정되지 않아야 한다.

    합성 목록에서는 후보를 일부러 여럿 넣어 만든 상황이다. 실제 코퍼스에서도 같은지 확인하지
    않으면, 코퍼스가 한 곳만 담고 있을 때 이 표현이 조용히 한 회사로 확정된다.
    """
    resolution = resolver.resolve_question(f"{mention} 2025년 매출 알려줘")

    assert resolution.companies == ()

    assert [item.raw for item in resolution.ambiguous] == [mention]


# ---------------------------------------------------------------------------
# 공개 경계 회귀
#
# 위쪽은 해석기까지만 본다. 해석기가 옳아도 그 결과가 Router를 지나며 canonical로 남지 않으면
# 실제 질문은 여전히 다른 회사를 답하거나 막히고, 그 어긋남은 해석기만 불러서는 드러나지 않는다.
# 여기서는 answer_question 한 번을 실제로 지나간다.
# ---------------------------------------------------------------------------

# representative_questions.md Q04 원문. 코퍼스에서 답이 되는 것이 확인된 질문이라
# 별칭 표기만 바꿔 견줄 수 있다.
Q04_CANONICAL_SPELLING = "삼성바이오로직스 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘."

# 같은 질문의 별칭 표기. COMPANY_ALIASES에 등록된 '삼바'다.
Q04_ALIAS_SPELLING = "삼바 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘."

# 계열사 접두어. 실제 코퍼스에서 후보가 여럿이며, 그중 하나를 고르지 않아야 한다.
AMBIGUOUS_PREFIX_QUESTION = "삼성 2025년 연결 기준 매출액이 얼마야?"


def _answer(question: str):
    return router.answer_question(
        question,
        corpus_root=_corpus_root(),
        manifest_path=_manifest_path(),
    )


def _carried_companies(result) -> set[str]:
    """결과가 실제로 담은 기업명. 표기 차이를 흡수해 비교한다."""
    names = [fact.company for fact in result.facts]

    names += [summary.company for summary in result.document_summaries]

    names += [event.company for event in result.events]

    return {normalize_company_name(name) for name in names if name}


def _blocked_at_query_plan(result) -> bool:
    return (
        result.execution_status is ExecutionStatus.BLOCKED
        and result.execution_issue is not None
        and result.execution_issue.stage == "query_plan"
    )


@pytest.mark.corpus
def test_alias_spelling_reaches_the_same_canonical_company_end_to_end() -> None:
    """
    별칭으로 적은 질문이 canonical 표기로 적은 질문과 같은 회사에 닿아야 한다.

    두 질문을 함께 돌려 견주는 이유:
        별칭 쪽만 보면 '답이 나왔다'까지만 알 수 있고, 그 답이 canonical 표기로 물었을 때와
        같은 회사인지는 알 수 없다. 사용자 표기가 그대로 Retrieval 조건이 되면 문서가 있는데도
        없는 것으로 답이 나가거나, 더 나쁘게는 다른 회사의 문서가 답이 된다.

    Pattern B가 실제로 답을 만드는지는 여기서 고정하지 않는다. 그것은 Q04의 코퍼스 테스트가
    맡고 있고, 여기서 함께 묶으면 요약 경로가 흔들릴 때 기업명 해석이 깨진 것처럼 보인다.
    """
    alias = _answer(Q04_ALIAS_SPELLING)

    canonical = _answer(Q04_CANONICAL_SPELLING)

    # 별칭 표기가 질의 해석 단계에서 막히지 않아야 한다. 막히면 등록된 별칭이 공개 경계까지
    # 이어지지 않은 것이다.
    assert not _blocked_at_query_plan(alias)

    assert alias.execution_status is canonical.execution_status

    assert _carried_companies(alias) == _carried_companies(canonical)

    if alias.execution_status is ExecutionStatus.COMPLETED:
        assert _carried_companies(alias) == {normalize_company_name("삼성바이오로직스")}


@pytest.mark.corpus
def test_ambiguous_prefix_is_blocked_at_the_public_boundary() -> None:
    """
    실제 코퍼스에서도 계열사 접두어는 계획 단계에서 막혀야 한다.

    사유를 함께 고정하는 이유:
        BLOCKED만 보면 '그런 기업 없음'으로 막힌 것과 구분되지 않는다. 실제로 일어난 일은
        후보가 여럿이라 고르지 않은 것이고, 두 사유가 한 이름으로 보고되면 여덟 곳이 있는
        질문에 '없다'고 답하게 된다.
    """
    result = _answer(AMBIGUOUS_PREFIX_QUESTION)

    assert result.execution_status is ExecutionStatus.BLOCKED

    assert result.execution_issue.stage == "query_plan"
    assert result.execution_issue.reason == AMBIGUOUS_COMPANY_REASON

    assert result.facts == []
    assert result.evidence == []
