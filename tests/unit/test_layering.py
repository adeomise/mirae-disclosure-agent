"""모듈 의존 방향 테스트.

지키려는 규칙:
    agent  ->  retrieval / periodic / event      (조합하는 쪽이 조합되는 쪽을 부른다)
    periodic, event  ->  agent 없음               (문서군 모듈은 질의 계층을 모른다)
    retrieval        ->  agent 없음               (검색은 질문 해석 결과를 모른다)
    common           ->  disclosure_agent 없음    (최하위. 누구나 쓸 수 있어야 한다)

event가 retrieval을 부르지 못하게 막아 둔 것은 의도적이다. linker가 직접 manifest를 뒤지기
시작하면 연결 규칙을 코퍼스 없이 시험할 수 없게 된다. 후보는 인자로 주입받는다.

되돌린 역전이 두 가지다.
- periodic/service.py가 QueryPlan을 받아 Evidence까지 조립하던 구조
- retrieval/metadata_filter.py가 QueryPlan을 직접 받던 시그니처

둘 다 사람이 리뷰에서 잡기 어려운 종류의 회귀라 테스트로 고정해 둔다.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "disclosure_agent"

FORBIDDEN_FOR_DOCUMENT_MODULES = ("disclosure_agent.agent", "disclosure_agent.retrieval")

# common이 부를 수 있는 유일한 패키지. 자기 안에서 나누는 것까지 막을 이유는 없다.
COMMON_PACKAGE = "disclosure_agent.common"


def _is_within(module: str, package: str) -> bool:
    """module이 package이거나 그 하위인가."""
    return module == package or module.startswith(f"{package}.")


def _imported_modules(source_path: Path) -> set[str]:
    """파일 하나가 import하는 모듈 이름을 모은다."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)

        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)

    return modules


def _python_files(package: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / package).glob("*.py"))


def test_package_root_is_discovered() -> None:
    """경로 계산이 틀리면 아래 테스트가 조용히 통과하므로 먼저 확인한다."""
    assert (PACKAGE_ROOT / "periodic" / "service.py").is_file()

    assert _python_files("periodic")


@pytest.mark.parametrize("source_path", _python_files("periodic"), ids=lambda path: path.name)
def test_periodic_does_not_import_upper_layers(source_path: Path) -> None:
    """periodic은 agent와 retrieval을 직접 import하지 않는다."""
    violations = [
        module
        for module in _imported_modules(source_path)
        for forbidden in FORBIDDEN_FOR_DOCUMENT_MODULES
        if module == forbidden or module.startswith(f"{forbidden}.")
    ]

    assert not violations, (
        f"{source_path.name}이 상위 계층을 import한다: {sorted(violations)}. "
        "문서군 모듈은 질의·조립 계층을 알면 안 된다."
    )


@pytest.mark.parametrize("source_path", _python_files("event"), ids=lambda path: path.name)
def test_event_does_not_import_upper_layers(source_path: Path) -> None:
    """event도 같은 규칙을 따른다. Pattern E를 붙일 때 방향이 어긋나지 않도록 미리 고정한다."""
    violations = [
        module
        for module in _imported_modules(source_path)
        for forbidden in FORBIDDEN_FOR_DOCUMENT_MODULES
        if module == forbidden or module.startswith(f"{forbidden}.")
    ]

    assert not violations, f"{source_path.name}이 상위 계층을 import한다: {sorted(violations)}"


@pytest.mark.parametrize("source_path", _python_files("retrieval"), ids=lambda path: path.name)
def test_retrieval_does_not_import_agent(source_path: Path) -> None:
    """
    검색 계층은 QueryPlan을 모른다.

    metadata_filter는 primitive 인자만 받고, 질문 해석 결과를 검색 조건으로 옮기는 일은
    agent handler가 한다.
    """
    violations = [
        module
        for module in _imported_modules(source_path)
        if module == "disclosure_agent.agent" or module.startswith("disclosure_agent.agent.")
    ]

    assert not violations, (
        f"{source_path.name}이 agent를 import한다: {sorted(violations)}. "
        "검색 조건은 primitive로 받아야 한다."
    )


@pytest.mark.parametrize("source_path", _python_files("common"), ids=lambda path: path.name)
def test_common_does_not_import_upper_layers(source_path: Path) -> None:
    """
    common은 최하위다.

    여기서 위쪽 계층을 부르면 어느 계층에서든 쓸 수 있다는 전제가 깨지고, event가
    retrieval을 우회해서 부르는 통로가 된다.

    common 안에서 나누는 것은 막지 않는다. schemas가 enums를 부르는 것이 그 예이며,
    계층을 거스르지 않는다.
    """
    violations = [
        module
        for module in _imported_modules(source_path)
        if module.startswith("disclosure_agent") and not _is_within(module, COMMON_PACKAGE)
    ]

    assert not violations, f"{source_path.name}이 상위 계층을 import한다: {sorted(violations)}"


def test_importing_periodic_does_not_pull_in_agent() -> None:
    """
    간접 의존까지 확인한다.

    직접 import가 없어도 periodic -> retrieval -> agent 같은 경로로 끌려오면 방향은 여전히
    뒤집혀 있다. 다른 테스트가 이미 로드해 둔 모듈에 영향받지 않도록 새 인터프리터에서 본다.
    """
    probe = (
        "import importlib, sys;"
        "importlib.import_module('disclosure_agent.periodic.service');"
        "importlib.import_module('disclosure_agent.retrieval.metadata_filter');"
        "leaked = sorted(m for m in sys.modules if m.startswith('disclosure_agent.agent'));"
        "print(','.join(leaked))"
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=PACKAGE_ROOT.parents[1],
        env={**__import__("os").environ, "PYTHONPATH": str(PACKAGE_ROOT.parent)},
    )

    assert completed.returncode == 0, completed.stderr

    leaked = completed.stdout.strip()

    assert leaked == "", f"periodic을 import했을 때 agent 모듈이 함께 로드된다: {leaked}"


def test_agent_may_import_lower_layers() -> None:
    """
    반대 방향은 허용된다. 규칙이 과하게 잠기지 않았는지 확인한다.

    공시 조회와 값 추출을 실제로 부르는 곳은 agent.period_retrieval이다. Pattern A와
    Pattern C가 그 모듈의 primitive를 공유하므로, 양성 대조군도 그 자리를 본다.
    """
    imported = _imported_modules(PACKAGE_ROOT / "agent" / "period_retrieval.py")

    assert "disclosure_agent.periodic.service" in imported
    assert "disclosure_agent.retrieval.metadata_filter" in imported
