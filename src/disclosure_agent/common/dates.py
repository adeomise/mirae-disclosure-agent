"""공시 날짜 표기 정규화 모듈.

역할:
- DART 문서에서 나오는 여러 날짜 표기를 비교 가능한 하나의 형태로 맞춘다.

왜 공용 위치에 두는가:
- manifest는 '20240830'을, 거래소공시 본문의 정정 블록은 '2024-08-30'을 쓴다.
- 검색 계층(retrieval)은 manifest 표기를, 이벤트 계층(event)은 본문 표기를 다루는데
  정정공시를 원공시에 연결하려면 두 표기를 같은 형태로 비교해야 한다.
- 두 계층이 각자 정규화 규칙을 복제하면 서로 조용히 어긋난다. 한쪽만 '2024.08.30'을
  받아들이게 되는 식의 차이가 생기면 연결 실패가 데이터 문제로 보인다.

이 모듈은 disclosure_agent의 다른 모듈을 import하지 않는다. 어느 계층에서든 쓸 수 있는
최하위 유틸리티이며, 공통 Schema나 Enum 같은 Data Contract가 아니다.
"""

from __future__ import annotations

import re
from typing import Any

# '20240830' / '2024-08-30' / '2024.08.30' / '2024/08/30'을 모두 받는다.
FILING_DATE_PATTERN = re.compile(r"^(\d{4})\D?(\d{2})\D?(\d{2})$")


def normalize_filing_date(value: Any) -> str | None:
    """
    공시일 표기를 YYYYMMDD로 맞춘다.

    해석할 수 없으면 추측하지 않고 None을 돌려준다. 날짜를 지어내면 정정공시가 엉뚱한
    원공시에 연결되고, 그 오류는 결과만 봐서는 드러나지 않는다.

    예:
        "2024-08-30" -> "20240830"
        "20240830"   -> "20240830"
        "작년 8월"    -> None
    """
    if value is None:
        return None

    match = FILING_DATE_PATTERN.match(str(value).strip())

    if not match:
        return None

    return "".join(match.groups())
