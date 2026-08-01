# Tests

- `unit`: 개별 Parser, Calculator, Safety 모듈 테스트 예정
- `integration`: 질문 입력부터 최종 응답까지 통합 테스트 예정
- `fixtures`: 테스트용 비식별 XML 저장 예정

실제 공모전 XML은 `fixtures`에 넣지 않습니다. 아직 기능이 구현되지 않았으므로 통과를 가장한
테스트는 만들지 않았으며, pytest가 테스트 미수집 외의 설정 오류 없이 프로젝트를 인식하도록
최소 설정만 `pyproject.toml`에 두었습니다.
