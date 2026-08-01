"""이벤트공시 XML Parser 모듈.

역할:
- 공급계약과 신규시설투자 공시의 구조 및 필드 후보를 보존한다.

예정 입력:
- XML 파일 경로
- DocumentMetadata

예정 출력:
- ParsedDocument와 Event 후보

TODO:
- Pilot XML별 태그와 표 구조 분석
- 이벤트 유형별 필드 매핑 구현
- 값, 단위, 날짜의 원문 표현 보존
"""
