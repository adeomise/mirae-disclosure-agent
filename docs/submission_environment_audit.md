# 제출 환경 감사 (Submission Environment Audit)

- 대상: `develop` @ `c3d4802` (`origin/develop`, `main` HEAD 와 동일 커밋)
- 일자: 2026-09-03
- 성격: 읽기 전용 감사. 수정·커밋·전체 pytest 실행 없음. 재현 실험은 저장소 밖 scratch 디렉터리에서 수행.
- 범위: Dockerfile / compose, pyproject / lock, FastAPI 기동 명령, 필수 환경 변수, HyperCLOVA X 설정,
  corpus root / manifest 설정, 절대 경로, Secret 리터럴, `.gitignore`, README 설치·실행 안내,
  fresh clone 재현성, `/api/ask` 기동 경로

---

## 요약

| 등급 | 건수 | 핵심 |
| --- | --- | --- |
| BLOCKER | 2 | Docker 이미지가 빌드되지 않음(README.md 누락) / 빌드돼도 실행할 프로세스가 없음(CMD 없음) |
| HIGH | 3 | 실행 명령이 어디에도 없음 / corpus 환경 변수 미문서화 + `.env` 미적용 / README 가 "미구현 scaffold" 라고 서술 |
| MEDIUM | 4 | HCX 설정 미문서화·오설정 무음 / `.dockerignore` 없음 / CWD 상대 기본값 / CI 없음 |
| LOW | 6 | 미사용 의존성, Dockerfile 핀 없음, `.gitignore` 누락 항목, import 시 StaticFiles 하드 실패, deprecation 경고, BLOCKED=422 |

Secret 리터럴, 커밋된 `.env`, 절대 경로는 **발견되지 않음**.

---

## BLOCKER

### B1. Docker 이미지가 빌드되지 않는다

- 파일: `Dockerfile:8-9`, `pyproject.toml:9`
- 문제: `COPY pyproject.toml uv.lock ./` 직후 `RUN uv sync --frozen --no-dev` 를 실행하는데,
  `pyproject.toml` 이 `readme = "README.md"` 를 선언한다. 이 시점에 README.md 가 컨텍스트에 없으므로
  hatchling 메타데이터 훅이 `OSError: Readme file does not exist: README.md` 로 중단된다.
- 검증: scratch 디렉터리에 `pyproject.toml` + `uv.lock` 만 두고 hatchling
  `prepare_metadata_for_build_editable` 실행 → 동일 OSError 재현. README.md 를 추가하고 `src/` 없이
  실행하면 editable wheel 빌드 성공 → 이 레이어의 유일한 원인은 README 누락.
- 영향: `docker build` 가 4번째 레이어에서 실패한다. 이미지를 만들 수 없다.
- 최소 수정: `COPY pyproject.toml uv.lock README.md ./`

### B2. 이미지에 실행할 프로세스가 없다

- 파일: `Dockerfile:1`, `Dockerfile:11`
- 문제: `CMD` / `ENTRYPOINT` / `EXPOSE` 가 없다. 1행 주석은 아직 "FastAPI 구현 후 entrypoint 추가 예정" 이라고
  적혀 있으나 앱은 `src/disclosure_agent/api/main.py:11` 에 이미 존재한다.
- 영향: B1 을 고쳐도 컨테이너가 즉시 종료된다.
- 최소 수정:

  ```dockerfile
  EXPOSE 8000
  CMD ["uv", "run", "--no-sync", "uvicorn", "disclosure_agent.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
  ```

---

## HIGH

### H1. 실행 명령이 저장소 어디에도 없다

- 파일: `README.md:67-68`, `scripts/run_api.sh:4`, `scripts/run_api.ps1:1`
- 문제: 두 실행 스크립트 모두 `"FastAPI application is not implemented yet."` 만 출력한다.
  모든 `.md`, 스크립트, Dockerfile 을 grep 해도 `uvicorn`, `/api/ask`, 기동 명령이 0건.
- 영향: 문서를 따라가는 평가자가 서버를 띄울 수 없다.
- 최소 수정: 스크립트 본문을 `uv run uvicorn disclosure_agent.api.main:app --host 0.0.0.0 --port 8000` 으로
  교체하고 README 에 같은 줄을 추가.

### H2. corpus 위치가 미문서화이며 `.env` 로는 설정되지 않는다

- 파일: `src/disclosure_agent/api/dependencies.py:8-18`, `.env.example:3`
- 문제:
  - 서비스는 `DISCLOSURE_CORPUS_ROOT` / `DISCLOSURE_CORPUS_MANIFEST` / `DATA_ROOT` 를 `os.environ` 에서만 읽는다.
  - 어디에서도 `load_dotenv` 를 호출하지 않는다(`python-dotenv` 는 pyproject 에만 등장). 따라서 `.env` 의
    `DATA_ROOT=./data` 는 무시된다. `.env` 에서 읽히는 것은 pydantic-settings 경유 `HYPERCLOVA_*` 뿐이다.
  - `.env.example` 에 `DISCLOSURE_CORPUS_*` 가 없다.
  - fresh clone 의 `data/` 는 `.gitkeep` 만 있다(`git ls-tree` 확인). 저장소 안에 `manifest.jsonl` 생성기는 없고
    소비자(`retrieval/metadata_filter.py:217`)만 있다.
- 검증: fresh clone 환경(모든 corpus·HCX 변수 unset)으로 앱을 in-process 실행 →
  `POST /api/ask` 가 모든 질문에 대해
  `422 {"execution_status":"BLOCKED","error":{"stage":"filing_discovery","reason":"manifest_missing",...}}`.
- 영향: API 는 "정상" 기동되지만 아무 질문에도 답하지 못하고, 422 는 클라이언트 오류처럼 읽힌다.
- 최소 수정: `.env.example` 과 README 에 아래를 추가.

  ```dotenv
  # 주최측 corpus 루트. manifest.jsonl 이 있어야 한다.
  # 주의: .env 가 아니라 셸 export 로 넘겨야 한다(현재 .env 는 HYPERCLOVA_* 만 로드).
  DISCLOSURE_CORPUS_ROOT=
  DISCLOSURE_CORPUS_MANIFEST=
  ```

### H3. README 가 시스템을 "미구현 scaffold" 로 서술한다

- 파일: `README.md:6-9`, `README.md:89-96`
- 문제: Parser, Retrieval, HyperCLOVA X, FastAPI, 평가 자동화를 "아직 구현되지 않음" 으로 나열하고
  "실행 가능한 API 도 제공하지 않습니다" 라고 명시한다.
- 영향: 첫 화면을 읽는 평가자가 실제 산출물을 저평가한다.
- 최소 수정: "아직 구현되지 않은 기능" 절 삭제, 도입부 문단 재작성. 나머지는 유지.

---

## MEDIUM

### M1. HyperCLOVA X 설정이 미문서화이고 오설정이 무음이다

- 파일: `.env.example:5-7`, `src/disclosure_agent/common/config.py:60-66`,
  `src/disclosure_agent/agent/hyperclova.py:100-102`, `src/disclosure_agent/api/service.py:261-266`
- 문제:
  - `HYPERCLOVA_ENABLED`, `HYPERCLOVA_TIMEOUT_SECONDS`, `HYPERCLOVA_MAX_RETRIES` 가 `.env.example` 에 없다.
  - `HYPERCLOVA_API_URL` 이 모델 경로까지 포함한 **완전한 endpoint** 여야 한다는 규칙은 docstring 에만 있다.
  - 호출 실패(404, 잘못된 키 등)는 service 에서 잡혀 결정론 답변으로 fallback 되며 WARNING 로그만 남는다.
    응답이나 health endpoint 어디에도 HCX 가 실제로 돌았는지 드러나지 않는다.
  - `.env` 는 CWD 기준(`env_file=".env"`)으로 읽힌다. Docker 는 `.env` 를 복사하지 않으므로(Secret 관점에선 올바름)
    `-e` / `--env-file` 로 넘겨야 한다.
- 영향: 데모가 HCX 없이 돌아가도 아무도 알아채지 못한다.
- 최소 수정: `.env.example` 에 추가 변수 3개와 완전한 URL 예시를 주석으로 문서화. 기동 시
  `HyperCLOVA X active: <bool>` 을 한 번 로그.

### M2. `.dockerignore` 가 없다

- 파일: 저장소 루트(부재 확인)
- 문제: 빌드 컨텍스트에 `.venv/`, `data/`, `.omc/`, 로컬 `.env` 가 모두 실린다. 현재는 `COPY src` 만 하므로
  이미지에 새지는 않지만 컨텍스트 업로드가 크고, 향후 `COPY . .` 한 줄이면 `.env` 가 이미지에 박힌다.
- 최소 수정: `.dockerignore` 추가 — `.venv`, `.env`, `.git`, `.omc`, `data`, `tests`, `evaluation/results`

### M3. CWD 상대 기본값

- 파일: `src/disclosure_agent/api/dependencies.py:11`, `src/disclosure_agent/common/config.py:49`
- 문제: `./data` 와 `.env` 가 uvicorn 을 실행한 디렉터리 기준으로 해석된다. 다른 디렉터리에서 기동하면
  corpus 와 자격 증명이 조용히 바뀐다.
- 최소 수정: README 에 "저장소 루트에서 실행" 명시, 또는 기본값을 `Path(__file__).resolve().parents[3]` 기준으로 고정.

### M4. CI 가 없다

- 파일: `.github/` (issue / PR 템플릿만 존재)
- 문제: fresh clone 이 green 임을 보여 주는 것이 없다.
- 최소 수정: workflow 하나 — `uv sync --frozen` → `uv run ruff check .` → `uv run pytest -m "not corpus"`

---

## LOW

### L1. `python-dotenv` 선언만 있고 미사용

- 파일: `pyproject.toml:20`
- 문제: import 되는 곳이 없다. `.env` 가 전역 로드된다는 잘못된 신호를 준다(H2 참조).
- 최소 수정: 제거하거나, `dependencies.py` 에서 실제로 `load_dotenv()` 호출.

### L2. Dockerfile 핀·PATH·사용자 부재

- 파일: `Dockerfile:2`, `Dockerfile:6`
- 문제: base 태그와 `pip install uv` 가 핀 없음. `ENV PATH="/app/.venv/bin:$PATH"` 없음. root 로 실행.
- 최소 수정: `pip install uv==<ver>`, PATH 라인 추가.

### L3. `.gitignore` 누락 항목

- 파일: `.gitignore`
- 문제: `.claude/`, `dist/`, `build/`, `*.egg-info` 가 없다. `.claude/settings.local.json`(로컬 절대 경로 포함)은
  현재 이 머신의 **전역** gitignore 덕분에만 untracked 이며, 다른 팀원의 clone 에서는 커밋될 수 있다.
- 최소 수정: `.gitignore` 에 `.claude/` 추가.

### L4. import 시 StaticFiles 하드 실패

- 파일: `src/disclosure_agent/api/main.py:15`
- 문제: `StaticFiles(directory=UI_DIR)` 가 import 시점에 실행되어 `ui/` 가 없으면 앱 import 자체가 실패한다.
  현재는 문제 없음(UI 파일 3개 tracked, `.gitignore:23` 의 `*.html` 예외 규칙 동작 확인). 다만 non-editable
  wheel 빌드에 `.html` 이 포함되는지는 미검증.

### L5. Starlette deprecation 경고

- `TestClient` import 시 `Using httpx with starlette.testclient is deprecated; install httpx2` 출력. 테스트는 정상 동작.

### L6. BLOCKED 전부가 HTTP 422

- 파일: `src/disclosure_agent/api/service.py:97-100`
- 문제: `manifest_missing` 을 포함한 모든 BLOCKED 가 422 로 나가 평가자가 "요청이 잘못됐다" 로 읽을 수 있다.
  `error.reason` 필드로 구분은 가능. `filing_discovery` 단계는 503 고려.

---

## 이상 없음으로 확인된 항목

- Secret 리터럴 없음. 유일한 매치는 `tests/unit/test_hyperclova_client.py:30` 의 가짜 키 `"super-secret-key"`
  (헤더 삽입·비유출 검증용).
- `.env` 가 어느 브랜치에도 커밋된 적 없음(`git log --all --diff-filter=A -- .env`).
- `src/`, `scripts/`, `tests/`, `docs/` 에 절대 경로 없음.
- API 키는 Authorization 헤더에만 실리고 예외 메시지·로그에 실리지 않음.
- Python 버전 핀 일치: `.python-version`(3.12) / pyproject(`>=3.12,<3.13`) / `uv.lock`(`==3.12.*`) /
  Dockerfile(`python:3.12-slim`).
- UI 는 `fetch("/api/ask")` 상대 경로 사용(`src/disclosure_agent/api/ui/app.js:172`). host 하드코딩 없음.
- `uv.lock` 의 프로젝트 항목이 pyproject 의존성 목록과 일치(육안 대조).

---

## Fresh clone 리허설 명령

```bash
git clone <repo-url> mirae-disclosure-agent && cd mirae-disclosure-agent && git switch develop
uv --version && uv python install 3.12
uv sync --frozen                          # uv.lock 이 stale 이면 여기서 실패
uv run ruff check .
uv run pytest -m "not corpus" -q          # corpus 불필요 subset

# corpus (주최측 데이터, manifest.jsonl 포함) — .env 가 아니라 셸 export
export DISCLOSURE_CORPUS_ROOT=/abs/path/to/corpus
# 선택: export DISCLOSURE_CORPUS_MANIFEST=/abs/path/to/manifest.jsonl

cp .env.example .env                      # HYPERCLOVA_API_KEY / _API_URL(완전한 endpoint) / _MODEL 채움
uv run uvicorn disclosure_agent.api.main:app --host 0.0.0.0 --port 8000 &
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
curl -s -X POST http://127.0.0.1:8000/api/ask -H 'Content-Type: application/json' \
     -d '{"question":"<Q01 유형 질문>"}'
uv run python scripts/run_corpus_regression.py --corpus-root "$DISCLOSURE_CORPUS_ROOT"

docker build -t disclosure-agent .        # 현재는 RUN uv sync 에서 실패 예상 (B1)
```

Windows PowerShell: `uv run uvicorn …` 전에 `$env:DISCLOSURE_CORPUS_ROOT = "E:\path\to\corpus"`.

---

## 검증 방법

### 코드·실행으로 검증한 것

- B1: scratch 디렉터리에서 hatchling 으로 재현(pyproject + uv.lock 만 → `OSError`; README 추가·`src/` 없음 → 성공).
- H2 / 기동 경로: 기존 `.venv` 에서 corpus·HCX 변수 전부 unset 한 채 `disclosure_agent.api.main` import →
  `GET /` 200, `GET /static/app.js` 200, `POST /api/ask` 422 `manifest_missing`,
  `HyperClovaSettings.is_active == False`.
- `develop` 의 tracked 파일 사실관계(`git ls-tree` / `git ls-files`): `data/` 는 `.gitkeep` 만, UI 파일 tracked,
  `.xml/.pdf` tracked 없음, `.env` 추가 이력 없음, `.dockerignore` 없음, compose 파일 없음, `.github/` 에 workflow 없음.
- grep 스윕: `load_dotenv`, `uvicorn`, `/api/ask`, 절대 경로 패턴, Secret 형태 리터럴.
- `uv.lock` 프로젝트 항목 vs pyproject 의존성 대조.

### 검증하지 못한 것

- 실제 `docker build` / `docker run` (Docker daemon 미기동). B1 은 uv 가 호출하는 동일한 hatchling 훅으로 추론.
- `uv lock --check` / `uv sync --frozen` (이 머신에 uv 미설치. 로컬 `.venv` 는 `python -m venv` 로 생성됨).
- pytest 실행 전반(요청에 따라 제외). `DISCLOSURE_CORPUS_ROOT` 를 읽는 unit 테스트가 unset 시 깔끔히 skip 되는지.
- 실제 corpus, 실제 `manifest.jsonl`, 실제 HyperCLOVA X 자격 증명으로의 동작.
- hatchling non-editable wheel 에 `ui/*.html` 이 포함되는지.
