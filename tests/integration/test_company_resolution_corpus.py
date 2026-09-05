"""실제 코퍼스 manifest로 확인하는 기업명 해석 회귀.

단위 테스트와 나누는 이유:
    tests/unit/test_company_resolution.py는 합성 목록으로 규칙을 고정한다. 규칙이 옳아도
    manifest가 상장명을 싣지 않거나 필드 이름이 다르면 실제 질문은 여전히 막힌다. 여기서
    보는 것은 그 연결이며, 규칙 자체는 다시 시험하지 않는다.

문서를 열지 않는 이유:
    기업명 해석은 manifest만으로 끝난다. 답까지 만드는 회귀는 Pattern별 코퍼스 테스트가 이미
    맡고 있고, 여기서 문서를 열면 파싱 실패가 기업명 해석 실패처럼 보인다.

코퍼스가 없으면 건너뛴다. 저장소에 실제 공시 파일을 두지 않기 때문이다(AGENTS.md).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from disclosure_agent.agent.company_resolution import (
    RESOLUTION_ALIAS,
    RESOLUTION_LISTED_NAME,
    RESOLUTION_UNIQUE_PARTIAL,
)
from disclosure_agent.agent.query_analyzer import build_company_resolver
from disclosure_agent.retrieval.metadata_filter import (
    list_company_names,
    list_company_universe,
    load_manifest,
)

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"


def _manifest_path() -> Path:
    raw_corpus_root = os.environ.get(CORPUS_ROOT_ENV)

    if not raw_corpus_root:
        pytest.skip(f"{CORPUS_ROOT_ENV} is not set")

    corpus_root = Path(raw_corpus_root).expanduser()

    if not corpus_root.is_dir():
        pytest.skip(f"corpus root is not a directory: {corpus_root}")

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
