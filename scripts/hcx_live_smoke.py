"""실제 자격 증명으로 HyperCLOVA X 전송 계층 하나만 확인하는 도구.

무엇을 확인하는가:
    설정한 자격 증명과 엔드포인트로 요청이 실제로 HyperCLOVA X에 닿고, 응답 본문을
    `agent.hyperclova`의 파서가 읽을 수 있는가. 그것 하나뿐이다.

무엇을 확인하지 않는가:
    답변 품질, 근거 payload 조립, 검증 통과 여부는 보지 않는다. 프롬프트도 시스템의
    것(verbalizer.SYSTEM_PROMPT)이 아니라 사소한 한국어 한 문장을 쓴다. 표현 단계의 실패와
    전송의 실패가 한 결과에 섞이면 "연동이 되긴 하는가"에 답할 수 없다.

왜 production 코드 밖에 두는가:
    실제 망을 타고 과금되는 호출이다. 테스트 스위트에 두면 자격 증명이 있는 환경에서 조용히
    같이 돌게 된다.

왜 raw requests를 쓰지 않는가:
    확인하려는 것이 바로 `HttpHyperClovaClient`의 헤더·본문·엔드포인트 조립이다. 여기서
    요청을 따로 만들면 실제 경로가 아닌 다른 것을 시험하게 된다. 그래서 post 자리만
    감싸 안전한 메타데이터를 기록하고, 나머지는 그대로 통과시킨다.

출력 정책:
    API 키와 요청/응답 헤더를 출력하지 않는다. 응답 본문은 앞부분만 잘라 보여 준다.
    사소한 프롬프트를 쓰므로 본문에 공시 수치가 실리지 않는다.

사용:
    python scripts/hcx_live_smoke.py
    python scripts/hcx_live_smoke.py --prompt "한 문장으로 인사해 주십시오." --json

종료 코드:
    0  요청이 HyperCLOVA X에 닿았고 본문을 읽었다
    1  요청은 나갔지만 실패했다(자격 증명, 한도, 엔드포인트, 응답 형식)
    2  설정이 비어 있어 요청을 만들지 않았다
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from disclosure_agent.agent.hyperclova import (  # noqa: E402
    HttpHyperClovaClient,
    HyperClovaError,
    _default_post,
    extract_finish_reason,
    resolve_endpoint,
)
from disclosure_agent.common.config import HyperClovaSettings  # noqa: E402

EXIT_OK = 0
EXIT_CALL_FAILED = 1
EXIT_NOT_CONFIGURED = 2

# 사소한 한국어 프롬프트. 전송만 확인하므로 답의 내용에 기대는 것이 없다.
DEFAULT_SYSTEM_PROMPT = "한국어로 한 문장만 답하십시오."
DEFAULT_USER_PROMPT = "안녕하세요라고 답해 주십시오."

# 응답 본문을 보여 주는 길이 상한.
PREVIEW_LENGTH = 200


class _ObservingPost:
    """실제 전송을 그대로 하고 안전한 메타데이터만 기록한다.

    헤더는 받기만 하고 어디에도 남기지 않는다. Authorization이 그 안에 있다.
    """

    def __init__(self) -> None:
        self.url: str | None = None
        self.status_code: int | None = None
        self.elapsed_ms: float | None = None
        self.body: Any = None

    def __call__(self, url: str, **kwargs: Any) -> Any:
        self.url = url

        started = time.perf_counter()

        try:
            response = _default_post(url, **kwargs)

        finally:
            self.elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

        self.status_code = response.status_code

        try:
            self.body = response.json()

        except ValueError:
            self.body = None

        return response


def _missing_credentials(settings: HyperClovaSettings) -> list[str]:
    """비어 있는 환경 변수 이름. 값은 옮기지 않는다."""
    return [
        f"HYPERCLOVA_{name.upper()}"
        for name, value in (
            ("api_key", settings.api_key),
            ("api_url", settings.api_url),
            ("model", settings.model),
        )
        if not value.strip()
    ]


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())

    if len(collapsed) <= PREVIEW_LENGTH:
        return collapsed

    return collapsed[:PREVIEW_LENGTH] + "…"


def _report(observer: _ObservingPost, settings: HyperClovaSettings) -> dict[str, Any]:
    """요청이 어디로 갔고 무엇이 돌아왔는지. 자격 증명은 담지 않는다."""
    body = observer.body if isinstance(observer.body, dict) else {}

    return {
        "endpoint": observer.url,
        "http_status": observer.status_code,
        "elapsed_ms": observer.elapsed_ms,
        "requested_model": settings.model,
        "api_status_code": (body.get("status") or {}).get("code")
        if isinstance(body.get("status"), dict)
        else None,
        "finish_reason": extract_finish_reason(observer.body),
        "max_tokens": settings.max_tokens,
        "timeout_seconds": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }


def run_smoke(*, system_prompt: str, user_prompt: str) -> tuple[int, dict[str, Any]]:
    settings = HyperClovaSettings()

    missing = _missing_credentials(settings)

    if missing:
        return EXIT_NOT_CONFIGURED, {
            "result": "not_configured",
            "missing_env": missing,
        }

    if not settings.enabled:
        return EXIT_NOT_CONFIGURED, {
            "result": "disabled",
            "detail": "HYPERCLOVA_ENABLED=false 이므로 요청을 만들지 않았습니다.",
        }

    observer = _ObservingPost()

    client = HttpHyperClovaClient(settings=settings, post=observer)

    # 요청을 보내기 전에 어디로 갈지 먼저 보여 준다. 실패했을 때 엔드포인트를 확인하려고
    # 다시 실행하지 않아도 된다.
    resolved = resolve_endpoint(settings.api_url, settings.model)

    try:
        content = client.complete(system_prompt=system_prompt, user_message=user_prompt)

    except HyperClovaError as error:
        return EXIT_CALL_FAILED, {
            "result": "failed",
            "error_kind": type(error).__name__,
            "error": str(error),
            "resolved_endpoint": resolved,
            **_report(observer, settings),
        }

    return EXIT_OK, {
        "result": "ok",
        "resolved_endpoint": resolved,
        "answer_preview": _preview(content),
        "answer_length": len(content),
        **_report(observer, settings),
    }


def _render_text(report: dict[str, Any]) -> str:
    lines = [f"{key}: {value}" for key, value in report.items() if value is not None]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--prompt", default=DEFAULT_USER_PROMPT)
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    code, report = run_smoke(system_prompt=args.system_prompt, user_prompt=args.prompt)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    else:
        print(_render_text(report))

    return code


if __name__ == "__main__":
    raise SystemExit(main())
