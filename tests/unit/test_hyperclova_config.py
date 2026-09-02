"""HyperCLOVA X 설정 계약 테스트.

여기서 고정하는 것은 하나다: 자격 증명이 갖춰지지 않은 환경에서 연동이 조용히 켜지지
않는다는 것. 켜졌다고 잘못 판정하면 첫 요청에서야 실패가 드러나고, 그 실패가 모델 장애와
구분되지 않는다.

모든 설정 객체를 _env_file=None으로 만든다. 실행 환경의 .env가 테스트 판정을 바꾸면
로컬에서만 통과하는 테스트가 된다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from disclosure_agent.common.config import HyperClovaSettings, get_hyperclova_settings

COMPLETE = {
    "api_key": "test-key",
    "api_url": "https://example.invalid/v3/chat-completions/HCX",
    "model": "HCX-005",
}


def settings(**overrides: object) -> HyperClovaSettings:
    """환경과 .env를 읽지 않는 설정 객체. 테스트가 실행 환경에 좌우되지 않게 한다."""
    return HyperClovaSettings(_env_file=None, **{**COMPLETE, **overrides})  # type: ignore[arg-type]


def test_complete_credentials_activate_the_integration() -> None:
    assert settings().is_configured
    assert settings().is_active


@pytest.mark.parametrize("missing", sorted(COMPLETE))
def test_any_missing_credential_leaves_the_integration_inactive(missing: str) -> None:
    """일부만 채워진 상태를 '설정됨'으로 보지 않는다."""
    incomplete = settings(**{missing: ""})

    assert not incomplete.is_configured
    assert not incomplete.is_active


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_whitespace_only_credentials_do_not_count_as_configured(blank: str) -> None:
    """공백만 든 환경 변수는 값이 아니다. .env에 키 이름만 남겨 둔 상태가 그렇다."""
    assert not settings(api_key=blank).is_configured


def test_disabled_flag_turns_off_a_fully_configured_integration() -> None:
    """자격 증명이 있어도 끌 수 있어야 한다. 결정론 경로만으로 대조하는 회귀 실행이 그렇다."""
    disabled = settings(enabled=False)

    assert disabled.is_configured
    assert not disabled.is_active


def test_missing_credentials_do_not_raise() -> None:
    """
    설정되지 않음은 오류가 아니다.

    여기서 예외를 던지면 키 없는 환경에서 결정론 경로만으로 답할 수 있는 질문까지 함께
    죽는다.
    """
    empty = HyperClovaSettings(_env_file=None, api_key="", api_url="", model="")

    assert not empty.is_active


@pytest.mark.parametrize("timeout", [0.0, -1.0, 61.0])
def test_timeout_outside_the_allowed_range_is_rejected(timeout: float) -> None:
    """상한 없는 타임아웃은 한 요청이 무한정 매달릴 수 있다는 뜻이다."""
    with pytest.raises(ValidationError):
        settings(timeout_seconds=timeout)


@pytest.mark.parametrize("retries", [-1, 4])
def test_retry_count_outside_the_allowed_range_is_rejected(retries: int) -> None:
    """재시도가 타임아웃과 곱해져 최악 지연을 분 단위로 만들지 않게 상한을 둔다."""
    with pytest.raises(ValidationError):
        settings(max_retries=retries)


def test_zero_retries_is_allowed_and_means_a_single_attempt() -> None:
    assert settings(max_retries=0).max_retries == 0


def test_settings_are_read_from_the_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """환경 변수 이름은 .env.example과 같은 HYPERCLOVA_ 접두사를 쓴다."""
    monkeypatch.setenv("HYPERCLOVA_API_KEY", "env-key")
    monkeypatch.setenv("HYPERCLOVA_API_URL", "https://example.invalid/env")
    monkeypatch.setenv("HYPERCLOVA_MODEL", "HCX-ENV")

    loaded = HyperClovaSettings(_env_file=None)  # type: ignore[call-arg]

    assert loaded.api_key == "env-key"
    assert loaded.model == "HCX-ENV"
    assert loaded.is_active


def test_cached_accessor_reads_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """요청마다 다시 읽으면 같은 질문이 서로 다른 경로로 답해질 수 있다."""
    get_hyperclova_settings.cache_clear()

    monkeypatch.setenv("HYPERCLOVA_MODEL", "HCX-FIRST")

    first = get_hyperclova_settings()

    monkeypatch.setenv("HYPERCLOVA_MODEL", "HCX-SECOND")

    assert get_hyperclova_settings() is first

    get_hyperclova_settings.cache_clear()
