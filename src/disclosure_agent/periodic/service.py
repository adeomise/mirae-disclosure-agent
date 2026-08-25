"""정기공시 처리 흐름 조정 모듈.

역할:
- Parser, Extractor, Chunker의 호출 순서와 결과 전달 경계를 관리한다.

현재 구현 범위:
- 정기공시 문서 하나를 읽어 targeted FinancialFact를 만드는 구간을 담당한다.

    본문 XML 경로
      -> ParsedDocument   (periodic.parser)
      -> FinancialFact    (periodic.extractor)

의존 방향에 대한 메모:
- 이 모듈은 agent와 retrieval을 import하지 않는다.
- 이전 구현은 여기서 QueryPlan을 받아 Evidence와 최종 결과까지 조립했다. 그러면 periodic이
  agent에 의존해 방향이 뒤집히고, Pattern이 늘 때마다 각 문서군 service가 같은 조립 코드를
  복제하게 된다. 질문 해석·공시 선택·Evidence 조립·결과 조립은 agent 계층의 책임이고,
  이 모듈은 "문서에서 무엇을 읽어낼 수 있는가"만 답한다.
- 공시를 고르는 일은 retrieval의 책임이므로 FilingRef가 아니라 경로를 받는다.

Parser 경계에 대한 메모:
- 이 모듈은 periodic.parser를 호출하기만 하고 수정하지 않는다.
- parser는 cell.acontext와 표 단위를 제공한다. 그래서 extractor는 연도와 연결/별도 기준을
  ACONTEXT로 결정론적으로 판정할 수 있다. 표 단위와 본표/주석 구분은 TABLE-GROUP까지
  따라가야 서는데, 그 연결은 extractor 쪽에서 한다.

    표 단위 선택
        parser의 table["unit"]은 표 본문의 행을 단위 선언으로 잘못 집어올 수 있다.
        extractor의 parse_unit이 그런 문자열을 trusted=False로 표시해 막고,
        resolve_table_unit이 표 자신의 선언 -> TABLE-GROUP 선언 -> 표 상속 단위 순서로
        신뢰 가능한 선언을 고른다.

    본표/주석 구분
        근거는 table_groups[].aclass에 있고 classify_table이 그 값을 읽는다.
        {XBRL} 이름공간의 접두 토큰만 해석한다. IS/BS/CF/EF는 본표, NT는 주석이며
        모르는 표기는 분류하지 않는다.

- 어느 쪽이든 값을 추측해 메우지 않는다. 신뢰 가능한 단위 선언이 없거나 본표를 특정할 수
  없으면 예외를 삼키거나 원문 XML을 다시 읽어 우회하지 않고 그대로
  ExtractionBlockedError를 올려 상위 단계가 BLOCKED로 보고하게 한다.


입력:
- 본문 XML 경로 또는 이미 파싱된 document dict

출력:
- ParsedDocument dict
- FinancialFact (Pattern A 내부 추출 표현)

Integration boundary:
- FinancialFact는 periodic 내부 표현으로 사용한다.
- Retrieval / Agent / API / Evaluation으로 전달되는 공통 출력은
  Contract의 Fact + Evidence를 기준으로 한다.
- FinancialFact -> Fact/Evidence 변환은 별도 adapter에서 담당한다.

TODO:
- FinancialFact -> Contract Fact/Evidence adapter 연결
- ACODE가 없는 문서의 targeted 추출 경로
- Chunker 연결
- 다중 Fact 추출 흐름 확장
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from disclosure_agent.periodic.extractor import FinancialFact, extract_financial_fact
from disclosure_agent.periodic.parser import parse_periodic_xml


def load_parsed_document(document_path: str | Path) -> dict[str, Any]:
    """
    정기공시 본문 XML 하나를 읽어 ParsedDocument dict로 만든다.

    파싱 실패는 여기서 삼키지 않는다. 호출자가 어느 단계에서 막혔는지 구분해 보고할 수
    있어야 하므로 parser가 올린 FileNotFoundError/ValueError를 그대로 통과시킨다.
    """
    return parse_periodic_xml(Path(document_path))


def extract_fact(
    document: dict[str, Any],
    *,
    metric: str,
    fiscal_year: int,
    accounting_scope: str,
) -> FinancialFact:
    """
    파싱된 문서에서 단일 재무 수치를 뽑는다.

    추출 규칙은 extractor가 가지고 있고 이 함수는 호출 경계만 고정한다.
    조건을 만족하는 값을 고를 수 없으면 ExtractionBlockedError가 올라온다.
    """
    return extract_financial_fact(
        document,
        metric=metric,
        fiscal_year=fiscal_year,
        accounting_scope=accounting_scope,
    )
