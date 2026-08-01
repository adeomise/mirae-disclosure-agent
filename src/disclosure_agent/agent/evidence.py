"""답변 근거 검증 모듈.

역할:
- 답변의 주장과 CalculationResult를 원문 문서, Section, Chunk에 연결한다.

예정 입력:
- RetrievedChunk, Fact, CalculationResult와 답변 초안

예정 출력:
- 검증된 Evidence 목록과 근거 부족 경고

TODO:
- 주장 단위 Evidence 연결 규칙 정의
- 인용 범위와 원문 일치 검증 구현
- 근거 없는 답변 차단 테스트 작성
"""
