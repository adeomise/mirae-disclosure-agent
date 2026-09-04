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

재시도 정책:
- 다시 물어서 달라질 수 있는 실패만 재시도한다(연결 실패, 타임아웃, 429, 5xx).
- 4xx는 재시도하지 않는다. 자격 증명이나 요청 형식이 틀린 것이며 같은 요청을 다시 보내도
  같은 답이 온다.
- 재시도 횟수는 설정 상한(HyperClovaSettings.max_retries)이 정한다.

출력 길이 정책:
- 출력 토큰 상한을 요청에 명시한다(HyperClovaSettings.max_tokens). 보내지 않으면 서버
  기본값이 정하는데, 그 값이 작으면 응답이 JSON을 닫지 못한 채 잘리고 검증 단계가 이를
  거절한다. 틀린 답이 나가지는 않지만 표현 단계가 조용히 무력해진다.

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


@dataclass(frozen=True)
class HttpHyperClovaClient:
    """HyperCLOVA X chat-completions 엔드포인트 클라이언트.

    settings.api_url은 모델 경로까지 포함한 완전한 엔드포인트로 받는다. 여기서 model 값을
    경로에 이어 붙이지 않는다. 배포마다 경로 규칙이 다르고, 코드가 규칙을 추측하면 URL이
    틀렸을 때 그 사실이 404로만 나타난다.

    요청 본문은 Chat Completions v3 규격이다. api_url도 v3 경로여야 한다.

        https://clovastudio.stream.ntruss.com/v3/chat-completions/{modelName}

    v1과 본문이 호환되지 않는다. 반복 패널티의 이름이 다르고(repetitionPenalty 대
    repeatPenalty) 범위도 다르므로, v1 경로에 이 본문을 보내면 그 값이 조용히 기본값으로
    적용된다. 코드가 경로에서 버전을 읽어 본문을 바꾸지는 않는다. 추측할 자리를 만들지 않고
    한 규격만 지원한다.
    """

    settings: HyperClovaSettings
    post: Post = field(default=_default_post)

    def complete(self, *, system_prompt: str, user_message: str) -> str:
        body = {
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
            "maxTokens": self.settings.max_tokens,
        }

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
                self.settings.api_url,
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
            raise HyperClovaRequestError(f"rejected with status {status}")

        try:
            payload = response.json()

        except ValueError as error:
            raise HyperClovaResponseError("response body is not JSON") from error

        return extract_message_content(payload)


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

    raise HyperClovaResponseError("no assistant content found in the response body")


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
