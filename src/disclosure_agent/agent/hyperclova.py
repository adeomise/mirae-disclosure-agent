"""HyperCLOVA X 연동 계약 모듈.

역할:
- 외부 모델 요청과 응답의 경계를 정의하고 Secret 및 원문 전송 정책을 적용한다.

공개 경계:
    ChatRequest -> ChatCompletionClient.complete -> str(모델이 돌려준 본문)

    이 모듈은 문자열을 주고 문자열을 받는다. 무엇을 보낼지(근거 payload 조립)와 받은 것을
    믿을지(응답 검증)는 여기서 정하지 않는다. 전송과 판정을 한 곳에 두면 "모델이 죽었다"와
    "모델이 틀린 말을 했다"가 같은 예외로 합쳐지고, 그 둘은 대응이 다르다. 앞은 다시 물으면
    되고, 뒤는 다시 물어도 안 된다.

전송 정책:
- 요청 본문과 응답 본문을 로그에 남기지 않는다. 본문에는 확보한 공시 수치가 들어 있고,
  로그는 저장소 밖으로 나간다. 남기는 것은 시도 횟수와 실패 종류뿐이다.
- API 키는 헤더에만 싣고 예외 메시지에 넣지 않는다.

엔드포인트 정책:
- 모델 이름은 URL에 실린다(v3: /v3/chat-completions/{modelName}). HYPERCLOVA_API_URL을
  모델까지 포함한 완전한 경로로 줘도 되고 기본 경로까지만 줘도 되며, 뒤쪽이면 여기서
  HYPERCLOVA_MODEL을 붙인다(resolve_endpoint).

재시도 정책:
- 다시 물어서 달라질 수 있는 실패만 재시도한다(연결 실패, 타임아웃, 429, 5xx).
- 4xx는 재시도하지 않는다. 자격 증명이나 요청 형식이 틀린 것이며 같은 요청을 다시 보내도
  같은 답이 온다.
- 재시도 횟수는 설정 상한(HyperClovaSettings.max_retries)이 정한다.

출력 길이 정책:
- 출력 토큰 상한을 요청에 명시한다(HyperClovaSettings.max_tokens). 보내지 않으면 서버
  기본값이 정하는데, 그 값이 작으면 응답이 JSON을 닫지 못한 채 잘리고 검증 단계가 이를
  거절한다. 틀린 답이 나가지는 않지만 표현 단계가 조용히 무력해진다.
- 상한을 부르는 이름은 모델군이 정한다(maxTokens 대 maxCompletionTokens). 틀린 이름은
  400으로 거절되며 4xx는 재시도하지 않는다.

모델별 요청 모양:
- settings.model로만 갈린다(MODEL_CAPABILITIES). 엔드포인트 경로나 이름 생김새로 추측하지
  않는다. HCX-007만 구조화 출력을 받으며, 그때 봉투를 서버가 강제한다(ANSWER_ENVELOPE_SCHEMA).
  지시문만으로는 지켜지지 않는다는 것이 HCX-005에서 확인됐다.

TODO:
- 공식 API 규격 확인 후 응답 파싱 경로 확정(현재는 v3 chat-completions 형태를 전제하고,
  본문 위치를 찾지 못하면 값을 추측하지 않고 HyperClovaResponseError로 멈춘다)
- 근거 payload 조립과 응답 검증 계층 연결
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from disclosure_agent.common.config import HyperClovaSettings

logger = logging.getLogger(__name__)

# 다시 물어서 달라질 수 있는 HTTP 상태.
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})


class HyperClovaError(RuntimeError):
    """외부 모델 연동이 답을 돌려주지 못한 경우.

    호출부는 이 예외를 답변 실패로 옮기지 않는다. 결정론 문장이 이미 있으므로, 외부 모델이
    없을 때의 동작은 '오류'가 아니라 '표현을 바꾸지 않음'이다.
    """


class HyperClovaUnavailableError(HyperClovaError):
    """연결·타임아웃·재시도 가능 상태 코드로 끝난 경우. 모델 쪽 사정이다."""


class HyperClovaRequestError(HyperClovaError):
    """4xx로 거절된 경우. 자격 증명이나 요청 형식 문제이며 재시도하지 않는다."""


class HyperClovaResponseError(HyperClovaError):
    """응답은 왔지만 본문을 찾지 못한 경우.

    빈 문자열로 대신하지 않는다. 빈 답변은 '모델이 아무 말도 하지 않았다'는 뜻이 되어,
    검증 단계가 통과시킬 수 있는 모양이 된다.
    """


class ChatCompletionClient(Protocol):
    """지시문과 사용자 메시지를 받아 모델 본문을 돌려준다."""

    def complete(self, *, system_prompt: str, user_message: str) -> str:
        """모델 응답 본문. 실패는 HyperClovaError로 알린다."""
        ...


Post = Callable[..., httpx.Response]


def _default_post(
    url: str,
    *,
    headers: Mapping[str, str],
    json: Mapping[str, Any],
    timeout: float,
) -> httpx.Response:
    """httpx로 한 번 POST한다. 주입 가능한 자리로 떼어 두어 테스트가 망을 타지 않는다."""
    with httpx.Client(timeout=timeout) as client:
        return client.post(url, headers=dict(headers), json=dict(json))


# v3 chat-completions의 모델 앞 경로. api_url이 여기까지만 주어졌을 때 settings.model을
# {modelName} 자리에 붙인다.
V3_CHAT_COMPLETIONS_PATH = "/v3/chat-completions"

# 출력 토큰 상한에 걸려 응답이 끊겼다는 finishReason.
TRUNCATED_FINISH_REASON = "length"

# 출력 토큰 상한을 부르는 이름. 모델군마다 다르고, 틀린 이름을 보내면 400으로 거절된다
# (HCX-007에 maxTokens를 보내면 40001 "Invalid parameter: maxTokens").
MAX_TOKENS_FIELD = "maxTokens"
MAX_COMPLETION_TOKENS_FIELD = "maxCompletionTokens"

# 구조화 출력을 쓸 때 추론을 끄는 값.
#
# HCX-007은 추론이 기본으로 켜져 있고, 추론과 구조화 출력은 함께 쓸 수 없다(둘 다 실으면
# 40001 "Invalid parameter: thinking, responseFormat"). 끄는 것이 손해가 아닌 이유는 이
# 호출의 목적이 판단이 아니라 옮겨 적기이기 때문이다. verbalizer.SYSTEM_PROMPT가 모델에게
# "판단하지 않고, 계산하지 않고, 조회하지 않는다"고 지시하는 것과 같은 방향이다.
THINKING_DISABLED: Mapping[str, str] = {"effort": "none"}

# 모델이 지켜야 하는 응답 봉투.
#
# verbalizer가 기대하는 모양과 같아야 한다(verbalizer.ModelAnswer, SYSTEM_PROMPT). 두 벌이
# 되면 한쪽만 바뀌었을 때 모델은 스키마를 지켰는데 파서가 거절하는 상태가 되고, 그 실패는
# "모델이 형식을 안 지킨다"로만 보인다. 어긋남은 테스트가 잡는다
# (test_envelope_schema_matches_the_model_answer_contract).
#
# used_*_ids를 required로 두는 이유: 비어 있어도 키는 있어야 한다. 키가 빠지면 파서가 신고
# 없음과 신고 실패를 구분하지 못한다.
ANSWER_ENVELOPE_SCHEMA: Mapping[str, Any] = {
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


@dataclass(frozen=True)
class ModelCapabilities:
    """모델이 받아 주는 요청 모양.

    엔드포인트 경로나 모델 이름의 생김새로 추측하지 않는다. 이름 규칙은 배포가 정하는 것이고,
    추측이 틀리면 400으로 거절되는데 4xx는 재시도하지 않으므로 연동이 켜져 있는데도 표현
    단계가 한 번도 채택되지 않는 상태가 조용히 이어진다. 아는 모델만 표에 적는다.
    """

    token_budget_field: str
    structured_output: bool


# 출력 토큰 상한 필드 이름이 maxTokens이고 구조화 출력이 없는 계약.
LEGACY_CAPABILITIES = ModelCapabilities(
    token_budget_field=MAX_TOKENS_FIELD,
    structured_output=False,
)

# HCX-007 계약. 출력 토큰 상한이 maxCompletionTokens이고 구조화 출력을 받는다.
REASONING_CAPABILITIES = ModelCapabilities(
    token_budget_field=MAX_COMPLETION_TOKENS_FIELD,
    structured_output=True,
)

MODEL_CAPABILITIES: Mapping[str, ModelCapabilities] = {
    "HCX-005": LEGACY_CAPABILITIES,
    "HCX-DASH-002": LEGACY_CAPABILITIES,
    "HCX-007": REASONING_CAPABILITIES,
}


def capabilities_for(model: str) -> ModelCapabilities:
    """
    이 모델에 보낼 요청 모양.

    모르는 이름은 LEGACY_CAPABILITIES로 둔다. 그것이 이 클라이언트가 지금까지 보내 온 모양이며,
    튜닝 모델처럼 표에 없는 배포의 동작을 바꾸지 않는다. 새 기능을 기본값으로 켜면 표에 없는
    모델이 전부 400으로 거절된다.
    """
    return MODEL_CAPABILITIES.get(model.strip(), LEGACY_CAPABILITIES)


def resolve_endpoint(api_url: str, model: str) -> str:
    """
    실제로 POST할 URL.

    HYPERCLOVA_MODEL을 설정에서 요구하면서 요청에는 쓰지 않으면, 모델을 바꿔 적어도 아무 일도
    일어나지 않고 기본 경로만 준 설정은 404로만 드러난다. 그래서 모델 이름이 URL에 실제로
    실리는 자리를 여기 하나로 둔다.

    세 가지만 구분한다. 그 밖의 경로는 손대지 않는다. 배포마다 경로 규칙이 다르고, 모르는
    모양을 추측해서 이어 붙이면 멀쩡한 설정을 깨뜨린다.

    - 이미 /{model}로 끝나면 완전한 엔드포인트다. 그대로 쓴다.
    - /v3/chat-completions로 끝나면 모델 자리가 비어 있는 것이다. /{model}을 붙인다.
    - 그 밖(예: 튜닝 모델의 /v3/tasks/{taskId}/chat-completions)은 그대로 쓴다.
    """
    url = api_url.strip().rstrip("/")

    if not url or not model.strip():
        return url

    name = model.strip()

    if url.endswith(f"/{name}"):
        return url

    if url.endswith(V3_CHAT_COMPLETIONS_PATH):
        return f"{url}/{name}"

    return url


@dataclass(frozen=True)
class HttpHyperClovaClient:
    """HyperCLOVA X chat-completions 엔드포인트 클라이언트.

    요청 본문은 Chat Completions v3 규격이다. api_url도 v3 경로여야 한다.

        https://clovastudio.stream.ntruss.com/v3/chat-completions/{modelName}

    api_url은 모델 경로까지 포함한 완전한 엔드포인트로 줘도 되고, v3 chat-completions
    기본 경로까지만 줘도 된다. 뒤쪽이면 settings.model을 {modelName} 자리에 붙인다
    (resolve_endpoint). 그 밖의 경로는 손대지 않는다.

    v1과 본문이 호환되지 않는다. 반복 패널티의 이름이 다르고(repetitionPenalty 대
    repeatPenalty) 범위도 다르므로, v1 경로에 이 본문을 보내면 그 값이 조용히 기본값으로
    적용된다. 코드가 경로에서 버전을 읽어 본문을 바꾸지는 않는다. 추측할 자리를 만들지 않고
    한 규격만 지원한다.

    같은 v3 안에서도 모델군마다 받는 필드가 다르다. 그 차이는 settings.model 하나로만 갈리며
    (MODEL_CAPABILITIES), 경로 모양으로 추측하지 않는다.

    - HCX-005 / HCX-DASH-002: maxTokens. 구조화 출력 없음.
    - HCX-007: maxCompletionTokens, responseFormat, thinking(effort=none).

    표에 없는 모델은 앞쪽 계약으로 둔다. 이 클라이언트가 지금까지 보내 온 모양이다.
    """

    settings: HyperClovaSettings
    post: Post = field(default=_default_post)

    @property
    def endpoint(self) -> str:
        """이번 호출이 실제로 POST할 URL."""
        return resolve_endpoint(self.settings.api_url, self.settings.model)

    @property
    def capabilities(self) -> ModelCapabilities:
        """이번 호출이 보낼 요청 모양."""
        return capabilities_for(self.settings.model)

    def build_request_body(self, *, system_prompt: str, user_message: str) -> dict[str, Any]:
        """
        실제로 보낼 본문.

        공통 필드는 모델과 무관하게 같다. 갈리는 것은 출력 토큰 상한의 이름과, 구조화 출력을
        실을 것인가 둘뿐이다.
        """
        capabilities = self.capabilities

        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            # 표현만 바꾸는 일이라 표본을 넓힐 이유가 없다. 같은 입력이 같은 문장으로
            # 옮겨져야 평가가 대조할 것이 생긴다.
            "temperature": 0.0,
            "topP": 1.0,
            # v3 철자는 repetitionPenalty다. v1의 repeatPenalty와 이름도 범위도 다르다
            # (v1: 0.0 초과 10.0 이하, 기본 5.0 / v3: 0.0 초과 2.0 이하, 기본 1.1).
            #
            # 값은 1.0으로 둔다. payload에 적힌 수치를 그대로 옮기는 것이 이 호출의 목적이라,
            # 같은 토큰이 다시 나오는 것을 억제할 이유가 없다. 기본값 1.1은 입력에 있던 숫자를
            # 다시 쓰는 쪽에 벌점을 주므로 이 용도와 방향이 반대다.
            "repetitionPenalty": 1.0,
            # 출력 길이를 서버 기본값에 맡기지 않는다. 기본값은 배포가 정하는 것이라 이쪽에서
            # 알 수 없고, 그 값이 작으면 응답이 JSON을 닫지 못한 채 잘린다. 잘린 응답은
            # verbalizer가 not_json으로 거절하므로 틀린 답이 나가지는 않지만, 연동이 정상인데도
            # 표현 단계가 한 번도 채택되지 않는 상태가 조용히 이어진다.
            #
            # 이름은 모델이 정한다(maxTokens 대 maxCompletionTokens). 값은 같은 설정에서 온다.
            capabilities.token_budget_field: self.settings.max_tokens,
        }

        if capabilities.structured_output:
            # 봉투를 지시문으로만 부탁하지 않고 서버가 강제하게 한다.
            #
            # 지시문만으로는 지켜지지 않는다. HCX-005는 같은 SYSTEM_PROMPT에 대해 내용은 맞고
            # 형식은 평문인 답을 돌려주고, 그 답은 parse_model_answer가 not_json으로 버린다.
            # 그러면 연동이 정상인데도 결정론 문장만 나가는 상태가 이어진다.
            body["thinking"] = dict(THINKING_DISABLED)
            body["responseFormat"] = {"type": "json", "schema": ANSWER_ENVELOPE_SCHEMA}

        return body

    def complete(self, *, system_prompt: str, user_message: str) -> str:
        body = self.build_request_body(system_prompt=system_prompt, user_message=user_message)

        attempts = self.settings.max_retries + 1

        last_error: HyperClovaError | None = None

        for attempt in range(1, attempts + 1):
            try:
                return self._attempt(body)

            except HyperClovaRequestError:
                # 같은 요청을 다시 보내도 같은 답이 온다.
                raise

            except HyperClovaError as error:
                last_error = error

                # 본문은 남기지 않는다. 남는 것은 몇 번째 시도가 어떤 종류로 실패했는가뿐이다.
                logger.warning(
                    "HyperCLOVA X call failed (attempt %d/%d): %s",
                    attempt,
                    attempts,
                    type(error).__name__,
                )

        raise last_error if last_error else HyperClovaUnavailableError("no attempt was made")

    def _attempt(self, body: Mapping[str, Any]) -> str:
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": uuid.uuid4().hex,
        }

        try:
            response = self.post(
                self.endpoint,
                headers=headers,
                json=body,
                timeout=self.settings.timeout_seconds,
            )

        except httpx.TimeoutException as error:
            raise HyperClovaUnavailableError("request timed out") from error

        except httpx.HTTPError as error:
            # 예외 문자열에 요청 본문이 실릴 수 있어 그대로 옮기지 않는다.
            raise HyperClovaUnavailableError(
                f"transport failure: {type(error).__name__}"
            ) from error

        status = response.status_code

        if status in RETRYABLE_STATUS_CODES:
            raise HyperClovaUnavailableError(f"retryable status {status}")

        if status >= 400:
            # 서버는 왜 거절했는지를 본문에 적어 보낸다. 그것을 버리면 남는 것이 숫자
            # 하나뿐이라, 자격 증명이 틀린 것(40104)과 파라미터가 범위를 벗어난 것(40000)이
            # 같은 문장으로 보인다. 둘은 대응이 다르고, 4xx는 재시도하지 않으므로 이 문장이
            # 운영자가 보는 전부다.
            code = extract_api_status_code(response)

            raise HyperClovaRequestError(
                f"rejected with status {status}"
                if code is None
                else f"rejected with status {status} (API status code {code})"
            )

        try:
            payload = response.json()

        except ValueError as error:
            raise HyperClovaResponseError("response body is not JSON") from error

        content = extract_message_content(payload)

        if extract_finish_reason(payload) == TRUNCATED_FINISH_REASON:
            # 본문은 남기지 않는다. 남기는 것은 '왜 표현 단계가 채택되지 않았는가'뿐이다.
            #
            # 끊긴 응답은 JSON 봉투가 닫히지 않아 verbalizer가 not_json으로 거절한다. 틀린
            # 답이 나가지는 않지만, 연동이 정상인데도 결정론 문장만 나가는 상태가 이어진다.
            # 그 원인이 출력 예산이라는 사실은 여기서만 알 수 있다.
            logger.warning(
                "HyperCLOVA X response hit the output budget (maxTokens=%d); "
                "the answer is truncated and will be rejected downstream",
                self.settings.max_tokens,
            )

        return content


def extract_message_content(payload: Any) -> str:
    """
    응답에서 모델 본문을 꺼낸다.

    v3 chat-completions의 result.message.content를 먼저 보고, 배포에 따라 달라지는 몇 가지
    형태를 순서대로 시도한다. 어느 것도 아니면 멈춘다. 여기서 빈 문자열이나 페이로드의
    문자열 아무것이나 골라 돌려주면, 검증 단계가 '모델이 그렇게 말했다'로 받아들인다.
    """
    for path in (
        ("result", "message", "content"),
        ("result", "output", "text"),
        ("message", "content"),
    ):
        content = _dig(payload, path)

        if isinstance(content, str) and content.strip():
            return content

    choices = _dig(payload, ("choices",))

    if isinstance(choices, Sequence) and not isinstance(choices, str | bytes) and choices:
        content = _dig(choices[0], ("message", "content"))

        if isinstance(content, str) and content.strip():
            return content

    # 본문 위치를 찾지 못한 이유가 대개 여기 적혀 있다. HTTP는 200인데 API가 본문 안에서
    # 거절한 경우(자격 증명, 한도 초과 등)가 그렇다. 코드만 옮긴다. status.message는 요청의
    # 일부를 되돌려줄 수 있고, 이 모듈은 요청 본문을 밖으로 내보내지 않는다.
    code = _dig(payload, ("status", "code"))

    if isinstance(code, str | int):
        raise HyperClovaResponseError(
            f"no assistant content found in the response body (API status code {code})"
        )

    raise HyperClovaResponseError("no assistant content found in the response body")


def extract_finish_reason(payload: Any) -> str | None:
    """
    응답이 왜 끝났는지. 알 수 없으면 None.

    끊긴 응답과 정상적으로 끝난 응답은 본문만 봐서는 구분되지 않는다. 둘 다 문자열이고,
    끊긴 쪽은 그냥 짧다. 이 값이 없으면 출력 예산 부족이 '모델이 형식을 안 지킨다'로만
    보인다.
    """
    for path in (
        ("result", "finishReason"),
        ("result", "finish_reason"),
        ("finishReason",),
    ):
        reason = _dig(payload, path)

        if isinstance(reason, str) and reason.strip():
            return reason.strip()

    choices = _dig(payload, ("choices",))

    if isinstance(choices, Sequence) and not isinstance(choices, str | bytes) and choices:
        reason = _dig(choices[0], ("finish_reason",))

        if isinstance(reason, str) and reason.strip():
            return reason.strip()

    return None


def extract_api_status_code(response: httpx.Response) -> str | int | None:
    """
    거절 응답이 본문에 적어 보낸 API 오류 코드. 없거나 JSON이 아니면 None.

    코드만 꺼낸다. status.message는 요청의 일부를 되돌려줄 수 있고, 이 모듈은 요청 본문을
    밖으로 내보내지 않는다(extract_message_content와 같은 정책).

    본문에 코드가 없는 거절도 있다(게이트웨이가 CLOVA Studio에 닿기 전에 끊는 경우). 그때
    None을 돌려주고 호출부는 HTTP 상태만 알린다. 없는 코드를 지어내지 않는다.
    """
    try:
        payload = response.json()

    except ValueError:
        return None

    code = _dig(payload, ("status", "code"))

    if isinstance(code, str | int) and not isinstance(code, bool):
        return code

    return None


def _dig(payload: Any, path: Sequence[str]) -> Any:
    """중첩 매핑에서 경로를 따라간다. 어긋나면 None."""
    current = payload

    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None

        current = current[key]

    return current


def build_chat_client(settings: HyperClovaSettings) -> ChatCompletionClient | None:
    """
    설정이 활성일 때만 클라이언트를 만든다.

    비활성이면 None이다. 예외를 던지지 않는 이유는 config 모듈과 같다. 자격 증명이 없는 것은
    오류가 아니라 '결정론 문장을 그대로 쓴다'는 뜻이다.
    """
    if not settings.is_active:
        return None

    return HttpHyperClovaClient(settings=settings)
