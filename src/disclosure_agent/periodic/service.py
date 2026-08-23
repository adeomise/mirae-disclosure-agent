"""정기공시 처리 흐름 조정 모듈.

역할:
- Parser, Extractor, Chunker의 호출 순서와 결과 전달 경계를 관리한다.

예정 입력:
- 정기공시 XML 경로와 DocumentMetadata

예정 출력:
- ParsedDocument, Fact 목록, Chunk 목록

TODO:
- 공통 Schema 확정 후 처리 흐름 구현
- 단계별 오류와 관측성 정책 연결
- 통합 테스트 작성
"""
