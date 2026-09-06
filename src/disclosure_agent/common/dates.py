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
from datetime import date
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


def parse_filing_date(value: Any) -> date | None:
    """
    공시일 표기를 date 객체로 맞춘다.

    normalize_filing_date와 같은 이유로 여기 둔다. 해석할 수 없으면 추측하지 않고 None을
    돌려준다 — 날짜를 지어내지 않는 규칙은 문자열 표기와 date 표기가 같아야 한다.

    "해석할 수 없다"에는 형식은 YYYYMMDD를 따르지만 실제 달력에 없는 값(예: "20260231",
    "20251310")도 포함한다. normalize_filing_date는 자릿수만 보고 통과시키므로, 그 결과를
    date(...)로 만드는 이 자리에서 걸러야 한다 — 그러지 않으면 이 함수가 ValueError로
    죽고, 그 예외가 그대로 위로 올라가 이 값을 쓰는 자리(SourceDocument.filing_date 등)
    전체의 생성을 실패시킨다. 날짜 하나가 표시용 metadata로 못 나가는 것과 응답 전체가
    죽는 것은 다르다.

    Contract 필드가 date 타입을 요구하는 자리(예: ResolvedEventState.as_of,
    SourceDocument.filing_date)에서 쓴다. 문자열 표기(YYYYMMDD)만 필요한 자리는
    normalize_filing_date를 그대로 쓴다.
    """
    normalized = normalize_filing_date(value)

    if normalized is None:
        return None

    try:
        return date(int(normalized[:4]), int(normalized[4:6]), int(normalized[6:]))
    except ValueError:
        # 자릿수는 맞지만(예: "20260231") 달력에 없는 값. 지어내지 않고 None.
        return None
