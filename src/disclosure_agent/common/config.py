"""애플리케이션 설정 계약 모듈.

역할:
- 환경 변수와 로컬 데이터 경로 설정의 경계를 정의한다.
- HyperCLOVA X 연동에 필요한 자격 증명과 호출 한계를 한 곳에서 읽는다.

이 모듈이 판정하는 것은 "연동이 켜져 있는가" 하나뿐이다. 무엇을 보낼지, 응답을 믿을지는
agent 계층이 정한다. common은 최하위 계층이라 상위 계층을 알 수 없고(tests/unit/test_layering.py),
설정이 판정을 겸하면 그 규칙이 깨진다.

입력:
- HYPERCLOVA_* 환경 변수 (.env 포함)

출력:
- 검증된 HyperClovaSettings

자격 증명이 없을 때 예외를 던지지 않는 이유:
    코퍼스 조회·계산·답변 가능성 판정은 외부 모델 없이 끝난다. 키가 없다고 기동을 막으면
    결정론 경로(agent.answer_builder)만으로 답할 수 있는 질문까지 함께 죽는다. 그래서
    '설정되지 않음'은 오류가 아니라 비활성 상태이며, 그때 호출부는 결정론 문장을 그대로 쓴다.

TODO:
- APP_ENV, LOG_LEVEL, DATA_ROOT의 설정 모델화 (현재는 호출부가 직접 읽는다)
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HyperClovaSettings(BaseSettings):
    """HyperCLOVA X 호출에 필요한 설정.

    api_key/api_url/model 셋이 모두 채워졌을 때만 연동이 성립한다. 하나라도 비면
    is_configured가 False가 되고, 호출부는 외부 요청을 만들지 않는다. 일부만 채워진 상태를
    '설정됨'으로 보면 첫 요청에서야 실패가 드러나고, 그 실패가 모델 장애와 구분되지 않는다.

    timeout과 max_retries를 설정에 두는 이유:
        둘 다 "얼마나 기다리고 몇 번 다시 물을 것인가"라는 운영 결정이며 코드가 정할 값이
        아니다. 다만 상한 없이 열어 두지는 않는다. 재시도가 늘어나면 같은 질문에 대한 응답
        시간이 사람이 예측할 수 없게 벌어지고, 평가에서 지연이 실패로 집계된다.
    """

    model_config = SettingsConfigDict(
        env_prefix="HYPERCLOVA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = ""
    api_url: str = ""
    model: str = ""

    # 켜 두더라도 자격 증명이 없으면 활성화되지 않는다. 이 값은 자격 증명이 갖춰진 환경에서
    # 결정론 경로만 쓰고 싶을 때(회귀 대조, 장애 재현) 끄는 스위치다.
    enabled: bool = True

    timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)

    # 재시도 '추가' 횟수다. 0이면 한 번만 호출한다. 상한을 3으로 둔 것은 그 이상이 타임아웃과
    # 곱해져 한 요청의 최악 지연을 분 단위로 만들기 때문이다.
    max_retries: int = Field(default=1, ge=0, le=3)

    @property
    def is_configured(self) -> bool:
        """외부 요청을 만들 수 있는 최소 정보가 갖춰졌는가."""
        return bool(self.api_key.strip() and self.api_url.strip() and self.model.strip())

    @property
    def is_active(self) -> bool:
        """이번 실행에서 HyperCLOVA X를 실제로 부를 것인가."""
        return self.enabled and self.is_configured


@lru_cache(maxsize=1)
def get_hyperclova_settings() -> HyperClovaSettings:
    """
    프로세스 수명 동안 한 번만 읽는 설정.

    요청마다 .env를 다시 읽지 않는다. 같은 프로세스 안에서 설정이 바뀌면 같은 질문이 서로
    다른 경로로 답해지고, 그 차이가 재현되지 않는다.

    테스트는 이 함수를 거치지 않고 HyperClovaSettings를 직접 만들거나
    get_hyperclova_settings.cache_clear()를 부른다.
    """
    return HyperClovaSettings()
