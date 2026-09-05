"""HyperCLOVA X transport 테스트.

여기서 고정하는 것:
    - 모델 이름이 요청 URL에 실제로 실린다.
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
    ANSWER_ENVELOPE_SCHEMA,
    LEGACY_CAPABILITIES,
    HttpHyperClovaClient,
    HyperClovaRequestError,
    HyperClovaResponseError,
    HyperClovaUnavailableError,
    build_chat_client,
    capabilities_for,
    endpoint_model_name,
    extract_api_status_code,
    extract_finish_reason,
    extract_message_content,
    resolve_endpoint,
)
from disclosure_agent.common.config import HyperClovaSettings

API_KEY = "super-secret-key"

ANSWER = "한미반도체(주)의 2025년 연결 기준 매출액은 576,684,619,527원입니다."

BASE_ENDPOINT = "https://example.invalid/v3/chat-completions"

# 튜닝 모델 경로. 마지막 segment가 모델 이름이 아니다.
TASK_ENDPOINT = "https://example.invalid/v3/tasks/abc123/chat-completions"


def endpoint_for(model: str) -> str:
    return f"{BASE_ENDPOINT}/{model}"


def make_settings(**overrides: object) -> HyperClovaSettings:
    """
    설정 객체. api_url을 따로 주지 않으면 model에 맞는 완전한 엔드포인트를 만든다.

    URL의 모델과 settings.model이 어긋난 설정은 이제 요청 전에 거절된다. 기본값을 한 모델로
    고정해 두면 모델을 바꿔 넣는 테스트가 전부 그 어긋남을 만들게 되고, 그 테스트들이 무엇을
    고정하려 했는지와 상관없이 실패한다. 어긋남은 그것을 시험하는 자리에서만 만든다.
    """
    values: dict[str, object] = {
        "api_key": API_KEY,
        "model": "HCX-005",
    }
    values.update(overrides)
    values.setdefault("api_url", endpoint_for(str(values["model"])))

    return HyperClovaSettings(
        _env_file=None,  # type: ignore[arg-type]
        **values,  # type: ignore[arg-type]
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


COMMON_BODY_KEYS = {"messages", "temperature", "topP", "repetitionPenalty"}

LEGACY_BODY_KEYS = COMMON_BODY_KEYS | {"maxTokens"}

REASONING_BODY_KEYS = COMMON_BODY_KEYS | {
    "maxCompletionTokens",
    "thinking",
    "responseFormat",
}


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("HCX-005", LEGACY_BODY_KEYS),
        ("HCX-DASH-002", LEGACY_BODY_KEYS),
        ("HCX-007", REASONING_BODY_KEYS),
        # 표에 없는 모델은 지금까지 보내 온 모양 그대로 둔다. 새 필드를 기본값으로 켜면
        # 튜닝 모델처럼 표에 없는 배포가 전부 400으로 거절된다.
        ("HCX-UNLISTED-001", LEGACY_BODY_KEYS),
    ],
)
def test_request_body_keys_are_a_fixed_set_per_model(model: str, expected: set[str]) -> None:
    """
    보내는 것이 무엇인지 모델별로 한 자리에 고정한다.

    키가 조용히 늘면 무엇을 외부로 내보내고 있는지가 코드를 읽어야만 드러난다. 모델별로
    갈리는 지금은 "어느 모델에 무엇을 보내는가"까지 함께 고정해야 한다. 필드 이름 하나가
    틀리면 400이고, 4xx는 재시도하지 않으므로 연동이 켜져 있는데도 표현 단계가 한 번도
    채택되지 않는 상태가 조용히 이어진다.
    """
    post = RecordingPost(json_response(200, ok_payload()))

    client(post, model=model).complete(system_prompt="S", user_message="U")

    assert set(post.calls[0]["json"]) == expected


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


# --------------------------------------------------------------------------
# 엔드포인트
# --------------------------------------------------------------------------


def test_model_name_reaches_the_request_url() -> None:
    """
    HYPERCLOVA_MODEL을 요구하면서 요청에 쓰지 않으면 모델을 바꿔 적어도 아무 일도 일어나지
    않는다. 어느 모델을 불렀는지가 URL에 남아야 한다.
    """
    post = RecordingPost(json_response(200, ok_payload()))

    client(post).complete(system_prompt="S", user_message="U")

    assert post.calls[0]["url"].endswith("/v3/chat-completions/HCX-005")


@pytest.mark.parametrize("model", ["HCX-005", "HCX-007"])
def test_base_v3_path_gets_the_model_appended(model: str) -> None:
    """기본 경로만 준 설정이 404로만 드러나지 않게 한다."""
    post = RecordingPost(json_response(200, ok_payload()))

    settings = make_settings(model=model).model_copy(update={"api_url": BASE_ENDPOINT})

    HttpHyperClovaClient(settings=settings, post=post).complete(
        system_prompt="S",
        user_message="U",
    )

    assert post.calls[0]["url"] == f"{BASE_ENDPOINT}/{model}"


@pytest.mark.parametrize(
    ("api_url", "model", "expected"),
    [
        # 모델 자리가 비어 있으면 settings.model을 붙인다.
        (BASE_ENDPOINT, "HCX-005", f"{BASE_ENDPOINT}/HCX-005"),
        (BASE_ENDPOINT, "HCX-007", f"{BASE_ENDPOINT}/HCX-007"),
        # 이미 완전하고 이름도 같으면 그대로 쓴다.
        (f"{BASE_ENDPOINT}/HCX-005", "HCX-005", f"{BASE_ENDPOINT}/HCX-005"),
        (f"{BASE_ENDPOINT}/HCX-007", "HCX-007", f"{BASE_ENDPOINT}/HCX-007"),
        # 뒤에 붙은 슬래시가 모델을 두 번 붙이거나 어긋남으로 보이게 만들지 않는다.
        (f"{BASE_ENDPOINT}/HCX-005/", "HCX-005", f"{BASE_ENDPOINT}/HCX-005"),
        (f"{BASE_ENDPOINT}/", "HCX-005", f"{BASE_ENDPOINT}/HCX-005"),
        # 모르는 경로 모양은 추측해서 이어 붙이지 않는다.
        (TASK_ENDPOINT, "HCX-005", TASK_ENDPOINT),
    ],
)
def test_endpoint_resolution_never_doubles_or_guesses(
    api_url: str,
    model: str,
    expected: str,
) -> None:
    assert resolve_endpoint(api_url, model) == expected


def test_endpoint_is_left_alone_without_a_model() -> None:
    """모델이 비면 '/'를 붙이지 않는다. 그 상태는 build_chat_client가 먼저 막는다."""
    assert resolve_endpoint(BASE_ENDPOINT, "  ") == BASE_ENDPOINT


# --------------------------------------------------------------------------
# 엔드포인트와 모델의 어긋남
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("api_url", "model"),
    [
        # 본문은 settings.model로 갈린다. 이 설정은 HCX-005에게 HCX-007 계약을 보낸다.
        (f"{BASE_ENDPOINT}/HCX-005", "HCX-007"),
        # 반대 방향도 같다. HCX-007에게 maxTokens를 보내면 40001이다.
        (f"{BASE_ENDPOINT}/HCX-007", "HCX-005"),
        (f"{BASE_ENDPOINT}/HCX-005/", "HCX-007"),
    ],
)
def test_endpoint_model_mismatch_is_rejected(api_url: str, model: str) -> None:
    """
    URL이 부르는 모델과 본문이 따르는 계약이 갈린 설정은 풀리지 않는다.

    그냥 두면 요청은 나가고 400으로 거절되며, 4xx는 재시도하지 않으므로 설정이 틀렸다는
    사실이 '모델이 형식을 안 지킨다'로만 보인다.
    """
    with pytest.raises(HyperClovaRequestError) as raised:
        resolve_endpoint(api_url, model)

    message = str(raised.value)

    assert model in message
    assert API_KEY not in message


@pytest.mark.parametrize(
    ("api_url", "model"),
    [
        (f"{BASE_ENDPOINT}/HCX-005", "HCX-007"),
        (f"{BASE_ENDPOINT}/HCX-007", "HCX-005"),
    ],
)
def test_endpoint_model_mismatch_fails_before_the_network(api_url: str, model: str) -> None:
    """
    어긋남은 POST 앞에서 멈춘다.

    요청이 한 번이라도 나가면 거절 사유가 서버에서 오게 되고, 그 사유는 자격 증명 오류나
    파라미터 오류와 같은 모양이라 설정이 틀렸다는 사실이 드러나지 않는다.
    """
    post = RecordingPost(json_response(200, ok_payload()))

    settings = make_settings(model=model).model_copy(update={"api_url": api_url})

    with pytest.raises(HyperClovaRequestError):
        HttpHyperClovaClient(settings=settings, post=post).complete(
            system_prompt="S",
            user_message="U",
        )

    assert post.calls == []


@pytest.mark.parametrize("model", ["HCX-005", "HCX-007"])
def test_task_endpoint_is_used_as_given_without_inferring_a_model(model: str) -> None:
    """
    모르는 경로의 마지막 segment를 모델 이름으로 읽지 않는다.

    읽으면 튜닝 모델의 /v3/tasks/{taskId}/chat-completions가 'chat-completions라는 모델과
    어긋난다'로 판정되어, 지금 동작하는 배포가 요청도 못 보내고 멈춘다.
    """
    post = RecordingPost(json_response(200, ok_payload()))

    settings = make_settings(model=model).model_copy(update={"api_url": TASK_ENDPOINT})

    HttpHyperClovaClient(settings=settings, post=post).complete(
        system_prompt="S",
        user_message="U",
    )

    assert post.calls[0]["url"] == TASK_ENDPOINT


@pytest.mark.parametrize(
    ("api_url", "expected"),
    [
        (f"{BASE_ENDPOINT}/HCX-005", "HCX-005"),
        (f"{BASE_ENDPOINT}/HCX-007", "HCX-007"),
        # 모델 자리가 비어 있는 기본 경로는 이름을 싣고 있지 않다.
        (BASE_ENDPOINT, None),
        # 튜닝 모델 경로와 그 밖의 모양은 아는 모양이 아니다.
        (TASK_ENDPOINT, None),
        (f"{BASE_ENDPOINT}/HCX-005/extra", None),
        # v1은 아는 모양이 아니다. 본문 규격도 다르고, 이 클라이언트는 v3만 보낸다.
        ("https://example.invalid/v1/chat-completions/HCX-005", None),
        ("", None),
    ],
)
def test_endpoint_model_name_is_read_only_from_the_known_shape(
    api_url: str,
    expected: str | None,
) -> None:
    assert endpoint_model_name(api_url) == expected


# --------------------------------------------------------------------------
# 종료 사유
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"result": {"finishReason": "length"}}, "length"),
        ({"result": {"finish_reason": "stop"}}, "stop"),
        ({"finishReason": "end_token"}, "end_token"),
        ({"choices": [{"finish_reason": "length"}]}, "length"),
        ({"result": {"message": {"content": ANSWER}}}, None),
        ({}, None),
    ],
)
def test_finish_reason_is_read_when_present(payload: Any, expected: str | None) -> None:
    assert extract_finish_reason(payload) == expected


def test_truncated_response_is_reported_instead_of_passing_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    끊긴 응답은 verbalizer가 not_json으로 거절한다. 틀린 답이 나가지는 않지만, 원인이 출력
    예산이라는 사실은 전송 계층에서만 알 수 있다. 그 자리를 비워 두면 연동이 정상인데도
    표현 단계가 한 번도 채택되지 않는 상태가 조용히 이어진다.
    """
    payload = ok_payload("{\"answer\": \"한미반도체(주)의 2025년 연결 기준 매")
    payload["result"]["finishReason"] = "length"

    post = RecordingPost(json_response(200, payload))

    with caplog.at_level(logging.WARNING, logger="disclosure_agent.agent.hyperclova"):
        client(post, max_retries=0).complete(system_prompt="S", user_message="U")

    logged = caplog.text

    # 상한을 부르는 이름은 모델군이 정한다. HCX-005는 maxTokens다.
    assert "maxTokens=512" in logged
    assert API_KEY not in logged
    assert "576,684,619,527" not in logged


def test_truncation_warning_names_the_field_the_model_actually_uses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    경고가 늘 maxTokens라고 적으면 HCX-007 운영자는 없는 필드를 찾게 된다.

    HCX-007에 maxTokens를 보내면 40001로 거절된다. 그 이름은 이 배포의 설정 어디에도 없다.
    """
    payload = ok_payload('{"answer": "한미반도체(주)의 2025년 연결 기준 매')
    payload["result"]["finishReason"] = "length"

    post = RecordingPost(json_response(200, payload))

    with caplog.at_level(logging.WARNING, logger="disclosure_agent.agent.hyperclova"):
        client(post, model="HCX-007", max_retries=0).complete(
            system_prompt="S",
            user_message="U",
        )

    logged = caplog.text

    assert "maxCompletionTokens=512" in logged
    assert "maxTokens=" not in logged


def test_completed_response_is_not_reported_as_truncated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = ok_payload()
    payload["result"]["finishReason"] = "stop"

    post = RecordingPost(json_response(200, payload))

    with caplog.at_level(logging.WARNING, logger="disclosure_agent.agent.hyperclova"):
        client(post, max_retries=0).complete(system_prompt="S", user_message="U")

    assert "maxTokens" not in caplog.text


def test_body_level_rejection_carries_the_api_status_code() -> None:
    """
    HTTP 200인데 API가 본문 안에서 거절한 경우다. 코드가 없으면 한도 초과와 형식 오류가
    같은 문장("본문을 찾지 못했다")으로 보인다.
    """
    with pytest.raises(HyperClovaResponseError) as raised:
        extract_message_content({"status": {"code": "42901", "message": "Too many requests"}})

    assert "42901" in str(raised.value)


def test_body_level_rejection_does_not_echo_the_api_message() -> None:
    """status.message는 요청의 일부를 되돌려줄 수 있다. 코드만 옮긴다."""
    with pytest.raises(HyperClovaResponseError) as raised:
        extract_message_content(
            {"status": {"code": "40000", "message": f"bad request: {ANSWER}"}}
        )

    assert "576,684,619,527" not in str(raised.value)


# --------------------------------------------------------------------------
# 거절 사유
# --------------------------------------------------------------------------


def test_rejection_carries_the_api_status_code_from_the_response_body() -> None:
    """
    4xx는 재시도하지 않으므로 이 예외 문장이 운영자가 보는 전부다.

    서버는 왜 거절했는지를 본문에 적어 보낸다. 그것을 버리고 숫자만 남기면 자격 증명이
    v3 규격이 아닌 것(40104)과 파라미터가 범위를 벗어난 것(40000)이 같은 문장으로 보이고,
    둘은 대응이 다르다. 앞은 키를 다시 발급받아야 하고 뒤는 본문을 고쳐야 한다.
    """
    post = RecordingPost(
        json_response(401, {"status": {"code": "40104", "message": "Invalid Key"}})
    )

    with pytest.raises(HyperClovaRequestError) as raised:
        client(post, max_retries=0).complete(system_prompt="S", user_message="U")

    message = str(raised.value)

    assert "401" in message
    assert "40104" in message
    assert API_KEY not in message


def test_rejection_does_not_echo_the_api_message() -> None:
    """status.message는 요청의 일부를 되돌려줄 수 있다. 코드만 옮긴다."""
    post = RecordingPost(
        json_response(400, {"status": {"code": "40000", "message": f"bad request: {ANSWER}"}})
    )

    with pytest.raises(HyperClovaRequestError) as raised:
        client(post, max_retries=0).complete(system_prompt="S", user_message="U")

    message = str(raised.value)

    assert "40000" in message
    assert "576,684,619,527" not in message


def test_rejection_without_a_body_code_still_reports_the_http_status() -> None:
    """
    코드가 없는 거절도 있다. 게이트웨이가 CLOVA Studio에 닿기 전에 끊으면 본문에 status가
    없거나 JSON이 아니다. 그때 없는 코드를 지어내지 않고 HTTP 상태만 알린다.
    """
    post = RecordingPost(httpx.Response(status_code=400, text="Bad Request"))

    with pytest.raises(HyperClovaRequestError) as raised:
        client(post, max_retries=0).complete(system_prompt="S", user_message="U")

    message = str(raised.value)

    assert "400" in message
    assert "API status code" not in message


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"status": {"code": "40104"}}, "40104"),
        ({"status": {"code": 40000}}, 40000),
        ({"status": {}}, None),
        ({"error": {"errorCode": "400"}}, None),
        ({}, None),
    ],
)
def test_api_status_code_is_read_only_when_the_body_carries_one(
    payload: Any,
    expected: str | int | None,
) -> None:
    assert extract_api_status_code(json_response(400, payload)) == expected


def test_api_status_code_of_a_non_json_body_is_absent() -> None:
    assert extract_api_status_code(httpx.Response(status_code=400, text="<html>")) is None


# --------------------------------------------------------------------------
# 모델별 요청 계약
# --------------------------------------------------------------------------


def body_for(model: str) -> dict[str, Any]:
    post = RecordingPost(json_response(200, ok_payload()))

    client(post, model=model).complete(system_prompt="S", user_message="U")

    return post.calls[0]["json"]


@pytest.mark.parametrize("model", ["HCX-005", "HCX-DASH-002", "HCX-UNLISTED-001"])
def test_legacy_models_keep_the_max_tokens_contract(model: str) -> None:
    """
    HCX-005 계약은 그대로다.

    HCX-007을 붙이면서 기존 모델의 본문이 함께 바뀌면, 지금 동작하는 배포가 400으로 멈춘다.
    maxCompletionTokens는 HCX-005에서 알 수 없는 필드다.
    """
    body = body_for(model)

    assert body["maxTokens"] == 512
    assert "maxCompletionTokens" not in body
    assert "responseFormat" not in body
    assert "thinking" not in body


def test_reasoning_model_uses_the_completion_token_budget() -> None:
    """
    HCX-007은 maxTokens를 모른다.

    실제로 보내면 400 40001 "Invalid parameter: maxTokens"로 거절된다. 4xx는 재시도하지
    않으므로 연동이 켜져 있는데도 표현 단계가 한 번도 채택되지 않는다.
    """
    body = body_for("HCX-007")

    assert body["maxCompletionTokens"] == 512
    assert "maxTokens" not in body


def test_reasoning_model_turns_thinking_off() -> None:
    """
    추론과 구조화 출력은 함께 쓸 수 없다.

    둘 다 실으면 400 40001 "Invalid parameter: thinking, responseFormat"이다. HCX-007은
    추론이 기본으로 켜져 있으므로, 끄는 값을 명시하지 않으면 구조화 출력을 쓸 수 없다.
    """
    assert body_for("HCX-007")["thinking"] == {"effort": "none"}


def test_reasoning_model_sends_the_full_envelope_schema() -> None:
    """
    봉투를 지시문으로만 부탁하지 않고 서버가 강제하게 한다.

    used_*_ids가 required에서 빠지면 모델이 그 키를 생략할 수 있고, 그러면 파서가 '신고
    없음'과 '신고 실패'를 구분하지 못한다.
    """
    response_format = body_for("HCX-007")["responseFormat"]

    assert response_format["type"] == "json"
    assert response_format["schema"] == {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "used_fact_ids": {"type": "array", "items": {"type": "string"}},
            "used_calculation_ids": {"type": "array", "items": {"type": "string"}},
            "used_evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "answer",
            "used_fact_ids",
            "used_calculation_ids",
            "used_evidence_ids",
        ],
        "additionalProperties": False,
    }


def test_reasoning_model_keeps_the_common_fields() -> None:
    """모델이 갈려도 결정성과 프롬프트 전달 방식은 같아야 한다."""
    body = body_for("HCX-007")

    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "S"
    assert body["messages"][1]["content"] == "U"
    assert body["temperature"] == 0.0
    assert body["topP"] == 1.0
    assert body["repetitionPenalty"] == 1.0


def test_envelope_schema_matches_the_model_answer_contract() -> None:
    """
    스키마와 파서가 같은 봉투를 말하는지 고정한다.

    두 벌이 되면 한쪽만 바뀌었을 때 모델은 스키마를 지켰는데 파서가 거절하는 상태가 되고,
    그 실패는 "모델이 형식을 안 지킨다"로만 보인다.
    """
    from dataclasses import fields

    from disclosure_agent.agent.verbalizer import ModelAnswer

    declared = {field.name for field in fields(ModelAnswer)}

    assert set(ANSWER_ENVELOPE_SCHEMA["properties"]) == declared
    assert set(ANSWER_ENVELOPE_SCHEMA["required"]) == declared


def test_capabilities_are_read_from_the_model_not_the_url() -> None:
    """
    경로 모양으로 능력을 추측하지 않는다.

    배포마다 경로 규칙이 다르고, 추측이 틀리면 400이다. 갈리는 근거는 settings.model 하나다.
    """
    assert capabilities_for("HCX-007").structured_output is True
    assert capabilities_for("HCX-005").structured_output is False

    # 앞뒤 공백은 설정에서 흔하다. 그것 때문에 계약이 조용히 바뀌지 않는다.
    assert capabilities_for("  HCX-007  ").structured_output is True

    # 모르는 이름은 지금까지 보내 온 계약으로 둔다.
    assert capabilities_for("HCX-UNLISTED-001") is LEGACY_CAPABILITIES
