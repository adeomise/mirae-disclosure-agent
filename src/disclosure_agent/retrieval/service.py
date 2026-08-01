"""검색 처리 흐름 조정 모듈.

역할:
- 메타데이터 필터, Keyword Search, 선택적 Vector Search와 결과 결합을 조정한다.

예정 입력:
- 분석된 질의와 검색 조건

예정 출력:
- 최종 RetrievedChunk 목록

TODO:
- 검색 구성요소 인터페이스 확정
- Vector Search 비활성화 경로 구현
- 검색 단계 통합 테스트 작성
"""
