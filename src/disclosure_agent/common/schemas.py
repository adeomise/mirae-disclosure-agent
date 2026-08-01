"""공통 Pydantic Schema 모듈.

역할:
- 문서, Chunk, Fact, Event, Evidence와 API 입출력 계약을 코드로 정의한다.

예정 입력:
- docs/data_contracts.md에서 팀이 확정한 JSON 계약

예정 출력:
- 담당 영역 사이에서 검증되고 직렬화 가능한 Pydantic v2 모델

TODO:
- Pilot XML 검토 후 필드와 선택 여부 확정
- 공통 Schema 변경에 대한 전원 합의
- 계약 검증 테스트 작성
"""
