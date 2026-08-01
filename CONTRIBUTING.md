# Contributing

## Branch

- `main`: 배포 가능한 기준선. 직접 Push 금지
- `develop`: 기능 통합 Branch
- `feat/periodic-slice`: 담당자 1 작업 Branch
- `feat/event-slice`: 담당자 2 작업 Branch
- `feat/runtime-api`: 담당자 3 작업 Branch

각 담당자는 자신의 Feature Branch에서 작업하고 Pull Request를 통해 `develop`에 병합합니다.
공통 Schema 변경은 구현 전에 전원이 합의해야 합니다. 큰 작업은 먼저 Issue를 생성합니다.

실제 공시 원문, 실제 기업 수치·접수번호, API Key 등 Secret은 커밋하지 않습니다. PR에는 작업
내용, 테스트 여부, 영향 범위를 기록합니다.

## Commit 예시

- `feat: add periodic XML parser`
- `feat: add event disclosure diff`
- `docs: define common data contract`
- `test: add periodic parser fixtures`
- `fix: preserve financial statement unit`

## Pull Request 전 확인

```bash
uv run ruff check .
uv run pytest
```
