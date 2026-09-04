"""HyperCLOVA X transport 테스트.

여기서 고정하는 것:
    - 다시 물어서 달라질 수 있는 실패만 재시도한다.
    - 응답 본문을 찾지 못하면 값을 추측하지 않고 멈춘다.
    - 요청/응답 본문과 API 키가 로그나 예외 메시지로 새지 않는다.

망을 타지 않는다. post 호출부를 주입받는 자리로 떼어 두었으므로 실제 엔드포인트 없이 규칙
전부를 시험할 수 있다.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from disclosure_agent.agent.hyperclova import (
    HttpHyperClovaClient,
    HyperClovaRequestError,
    HyperClovaResponseError,
    HyperClovaUnavailableError,
    build_chat_client,
    extract_message_content,
)
from disclosure_agent.common.config import HyperClovaSettings

API_KEY = "super-secret-key"

ANSWER = "한미반도체(주)의 2025년 연결 기준 매출액은 576,684,619,527원입니다."


def make_settings(**overrides: object) -> HyperClovaSettings:
    return HyperClovaSettings(
        _env_file=None,  # type: ignore[arg-type]
        api_key=API_KEY,
        api_url="https://example.invalid/v3/chat-completions/HCX-005",
        model="HCX-005",
        **overrides,  # type: ignore[arg-type]
    )


def json_response(status: int, payload: Any) -> httpx.Response:
    return httpx.Response(status_code=status, json=payload)


def ok_payload(content: str = ANSWER) -> dict[str, Any]:
    return {
        "status": {"code": "20000"},
        "result": {"message": {"role": "assistant", "content": content}},
    }


class RecordingPost:
    """호출을 기록하고 미리 정한 결과를 차례로 돌려준다."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})

        outcome = self.outcomes.pop(0)

        if isinstance(outcome, Exception):
            raise outcome

        return outcome


def client(post: RecordingPost, **overrides: object) -> HttpHyperClovaClient:
    return HttpHyperClovaClient(settings=make_settings(**overrides), post=post)


# --------------------------------------------------------------------------
# 정상 경로
# --------------------------------------------------------------------------


def test_successful_call_returns_the_model_content() -> None:
    post = RecordingPost(json_response(200, ok_payload()))

    assert client(post).complete(system_prompt="S", user_message="U") == ANSWER
    assert len(post.calls) == 1


def test_request_carries_the_credentials_in_headers_only() -> None:
    """키는 헤더에만 실린다. 본문에 섞이면 로깅 정책을 지켜도 새어 나갈 자리가 는다."""
    post = RecordingPost(json_response(200, ok_payload()))

    client(post).complete(system_prompt="S", user_message="U")

    call = post.calls[0]

    assert call["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in str(call["json"])


def test_request_is_deterministic_by_configuration() -> None:
    """표현만 바꾸는 일이라 표본을 넓히지 않는다. 같은 입력이 같은 문장이 되어야 대조가 된다."""
    post = RecordingPost(json_response(200, ok_payload()))

    client(post).complete(system_prompt="S", user_message="U")

    assert post.calls[0]["json"]["temperature"] == 0.0


def test_prompt_and_payload_are_sent_as_separate_roles() -> None:
    post = RecordingPost(json_response(200, ok_payload()))

    client(post).complete(system_prompt="SYSTEM", user_message="USER")

    messages = post.calls[0]["json"]["messages"]

    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[0]["content"] == "SYSTEM"
    assert messages[1]["content"] == "USER"


def test_output_budget_is_sent_with_the_request() -> None:
    """
    출력 길이를 서버 기본값에 맡기지 않는다.

    맡기면 그 값이 작을 때 응답이 JSON을 닫지 못한 채 잘리고, verbalizer가 not_json으로
    거절한다. 틀린 답이 나가지는 않지만 표현 단계가 조용히 무력해진다.
    """
    post = RecordingPost(json_response(200, ok_payload()))

    client(post, max_tokens=777).complete(system_prompt="S", user_message="U")

    assert post.calls[0]["json"]["maxTokens"] == 777


def test_default_output_budget_reaches_the_request_body() -> None:
    """설정을 건드리지 않은 실행도 상한을 명시한다. 기본값이 전송되지 않으면 고친 것이 없다."""
    post = RecordingPost(json_response(200, ok_payload()))

    client(post).complete(system_prompt="S", user_message="U")

    assert post.calls[0]["json"]["maxTokens"] == 512


def test_request_body_keys_are_a_fixed_set() -> None:
    """
    보내는 것이 무엇인지 한 자리에 고정한다.

    키가 조용히 늘면 무엇을 외부로 내보내고 있는지가 코드를 읽어야만 드러난다.
    """
    post = RecordingPost(json_response(200, ok_payload()))

    client(post).complete(system_prompt="S", user_message="U")

    assert set(post.calls[0]["json"]) == {
        "messages",
        "temperature",
        "topP",
        "repetitionPenalty",
        "maxTokens",
    }


def test_repetition_penalty_uses_the_v3_spelling_and_range() -> None:
    """
    v3 규격은 repetitionPenalty이며 v1의 repeatPenalty와 이름도 범위도 다르다.

    v1 이름을 보내면 그 값이 조용히 무시되고 서버 기본값이 적용된다. 기본값은 입력에 있던
    토큰을 다시 쓰는 쪽에 벌점을 주므로, 확보한 수치를 그대로 옮겨야 하는 이 호출과 방향이
    반대다. 틀린 답이 나가지는 않지만 검증 단계에서 걸릴 확률이 올라간다.
    """
    post = RecordingPost(json_response(200, ok_payload()))

    client(post).complete(system_prompt="S", user_message="U")

    body = post.calls[0]["json"]

    assert "repeatPenalty" not in body
    assert body["repetitionPenalty"] == 1.0

    # v3 허용 범위: 0.0 초과 2.0 이하.
    assert 0.0 < body["repetitionPenalty"] <= 2.0


def test_sampling_parameters_stay_inside_the_v3_ranges() -> None:
    """
    보내는 값이 규격 범위 안에 있는지 고정한다.

    범위를 벗어난 값은 400으로 거절되고, 400은 재시도하지 않으므로 결정론 문장으로 조용히
    돌아간다. 연동이 켜져 있는데 표현 단계가 한 번도 채택되지 않는 상태가 그렇게 만들어진다.
    """
    post = RecordingPost(json_response(200, ok_payload()))

    client(post).complete(system_prompt="S", user_message="U")

    body = post.calls[0]["json"]

    # v3 temperature는 0.00 이상 1.00 이하로 0을 포함한다(v1은 0을 제외한다).
    assert 0.0 <= body["temperature"] <= 1.0

    # v3 topP는 0.00 초과 1.00 이하.
    assert 0.0 < body["topP"] <= 1.0

    # v3 maxTokens는 1 이상이며 HCX-005 상한이 4096이다.
    assert 1 <= body["maxTokens"] <= 4096


def test_configured_timeout_reaches_the_transport() -> None:
    post = RecordingPost(json_response(200, ok_payload()))

    client(post, timeout_seconds=3.5).complete(system_prompt="S", user_message="U")

    assert post.calls[0]["timeout"] == 3.5


# --------------------------------------------------------------------------
# 재시도
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_status_is_retried_up_to_the_configured_limit(status: int) -> None:
    post = RecordingPost(json_response(status, {}), json_response(200, ok_payload()))

    assert client(post, max_retries=1).complete(system_prompt="S", user_message="U") == ANSWER
    assert len(post.calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_error_is_not_retried(status: int) -> None:
    """자격 증명이나 요청 형식이 틀린 것이며 같은 요청을 다시 보내도 같은 답이 온다."""
    post = RecordingPost(json_response(status, {}), json_response(200, ok_payload()))

    with pytest.raises(HyperClovaRequestError):
        client(post, max_retries=3).complete(system_prompt="S", user_message="U")

    assert len(post.calls) == 1


def test_timeout_is_retried_then_reported_as_unavailable() -> None:
    post = RecordingPost(
        httpx.TimeoutException("timed out"),
        httpx.TimeoutException("timed out"),
    )

    with pytest.raises(HyperClovaUnavailableError):
        client(post, max_retries=1).complete(system_prompt="S", user_message="U")

    assert len(post.calls) == 2


def test_transport_failure_is_reported_without_the_request_body() -> None:
    post = RecordingPost(httpx.ConnectError("connection refused"))

    with pytest.raises(HyperClovaUnavailableError) as raised:
        client(post, max_retries=0).complete(system_prompt="S", user_message="U")

    assert API_KEY not in str(raised.value)


def test_zero_retries_means_a_single_attempt() -> None:
    post = RecordingPost(json_response(503, {}))

    with pytest.raises(HyperClovaUnavailableError):
        client(post, max_retries=0).complete(system_prompt="S", user_message="U")

    assert len(post.calls) == 1


def test_retry_limit_is_bounded_by_the_setting() -> None:
    post = RecordingPost(*[json_response(503, {}) for _ in range(10)])

    with pytest.raises(HyperClovaUnavailableError):
        client(post, max_retries=3).complete(system_prompt="S", user_message="U")

    assert len(post.calls) == 4


# --------------------------------------------------------------------------
# 응답 파싱
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"result": {"message": {"content": ANSWER}}},
        {"result": {"output": {"text": ANSWER}}},
        {"message": {"content": ANSWER}},
        {"choices": [{"message": {"content": ANSWER}}]},
    ],
)
def test_known_response_shapes_are_read(payload: dict[str, Any]) -> None:
    assert extract_message_content(payload) == ANSWER


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"result": {}},
        {"result": {"message": {"content": "   "}}},
        {"result": {"message": {"content": None}}},
        {"choices": []},
        {"status": {"code": "40000"}},
    ],
)
def test_unreadable_response_stops_instead_of_returning_an_empty_answer(
    payload: dict[str, Any],
) -> None:
    """
    빈 문자열로 대신하지 않는다.

    빈 답변은 모델이 아무 말도 하지 않았다는 뜻이 되어, 검증 단계가 통과시킬 수 있는 모양이
    된다.
    """
    with pytest.raises(HyperClovaResponseError):
        extract_message_content(payload)


def test_non_json_body_is_reported_as_a_response_error() -> None:
    post = RecordingPost(httpx.Response(status_code=200, text="<html>error</html>"))

    with pytest.raises(HyperClovaResponseError):
        client(post, max_retries=0).complete(system_prompt="S", user_message="U")


# --------------------------------------------------------------------------
# 로깅 정책
# --------------------------------------------------------------------------


def test_failure_logging_never_carries_the_payload_or_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """본문에는 확보한 공시 수치가 들어 있고, 로그는 저장소 밖으로 나간다."""
    post = RecordingPost(json_response(503, {}), json_response(200, ok_payload()))

    with caplog.at_level(logging.DEBUG):
        client(post, max_retries=1).complete(
            system_prompt="S",
            user_message="SECRET-PAYLOAD-576684619527",
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)

    assert API_KEY not in logged
    assert "SECRET-PAYLOAD" not in logged
    assert "576684619527" not in logged
    assert "HyperClovaUnavailableError" in logged


# --------------------------------------------------------------------------
# 구성
# --------------------------------------------------------------------------


def test_inactive_settings_produce_no_client() -> None:
    """자격 증명이 없는 것은 오류가 아니라 결정론 문장을 그대로 쓴다는 뜻이다."""
    empty = HyperClovaSettings(_env_file=None, api_key="")  # type: ignore[arg-type]

    assert build_chat_client(empty) is None


def test_disabled_settings_produce_no_client() -> None:
    assert build_chat_client(make_settings(enabled=False)) is None


def test_active_settings_produce_a_client() -> None:
    assert isinstance(build_chat_client(make_settings()), HttpHyperClovaClient)
