# Git Workflow

```text
main
└── develop
    ├── feat/periodic-slice
    ├── feat/event-slice
    └── feat/runtime-api
```

1. `develop`을 최신화하고 담당 Feature Branch를 만듭니다.
2. 작은 단위로 Commit하고 Ruff와 pytest를 실행합니다.
3. 작업 내용, 테스트 여부, 영향 범위를 적은 PR을 `develop` 대상으로 생성합니다.
4. 리뷰와 CI 확인 후 병합합니다.
5. 통합 검증을 통과한 버전만 `develop`에서 `main`으로 병합합니다.

`main` 직접 Push와 실제 공시 원문·Secret Commit은 금지합니다. 공통 Schema 변경은 코드보다
합의와 `docs/data_contracts.md` 갱신이 먼저입니다.
