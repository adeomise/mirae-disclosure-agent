# 배포 (최소 구성)

이 문서는 `Linux VM + Docker`에서 FastAPI 서버를 띄우는 **최소** 절차만 다룹니다. 이 문서가
정하는 것은 실행 방법뿐이고, `/api/ask`의 답변 로직·판정 계약은 바꾸지 않습니다(정본은
`docs/agent_design/`).

```text
Internet -> VM public IP:8000 -> Docker container -> FastAPI -> mounted corpus
```

Nginx/TLS/CORS/multi-worker는 이번 최소 구성에 포함하지 않습니다. evaluator가 IP:8000으로
서버 대 서버 호출을 한다고 가정합니다.

## 1. Docker build

```bash
docker build -t disclosure-agent:local .
```

`Dockerfile`은 `uv sync --frozen --no-dev`로 `uv.lock` 그대로 의존성을 설치하고, 코드
레이어는 그 뒤에 별도로 `COPY`합니다. 코퍼스와 `.env`는 이미지에 들어가지 않습니다
(`.dockerignore`가 `data/`, `.env`를 build context에서 제외합니다). 둘 다 아래처럼
런타임에 주입합니다.

## 2. 필요한 환경 변수

| 변수 | 의미 | 예시 |
|---|---|---|
| `DISCLOSURE_CORPUS_ROOT` | 컨테이너 안에서 코퍼스 원문이 있는 경로 | `/data/corpus` |
| `DISCLOSURE_CORPUS_MANIFEST` | manifest 파일 경로(`DISCLOSURE_CORPUS_ROOT`와 분리 배치 시) | `/data/corpus/manifest.jsonl` |
| `HYPERCLOVA_API_KEY` / `HYPERCLOVA_API_URL` / `HYPERCLOVA_MODEL` | HyperCLOVA X 연동 자격 증명·엔드포인트(`common/config.py`) | — |

**두 env 그룹의 로딩 경로가 다르므로 섞어서 이해하면 안 됩니다.**

- `DISCLOSURE_CORPUS_ROOT`/`DISCLOSURE_CORPUS_MANIFEST`는 `api/dependencies.py`가
  `os.environ`에서 **직접** 읽습니다. 로컬(비-Docker) 실행에서 이 두 값을 `.env` 파일에
  적어 두는 것만으로는 자동으로 적용되지 않습니다 — 그 값이 실제로 프로세스의 환경 변수로
  export되어 있어야 합니다(예: `export DISCLOSURE_CORPUS_ROOT=...` 후 실행, 또는 실행 셸에서
  직접 설정). Docker에서는 다릅니다. `docker run -e ...` 또는 `--env-file`로 넘기면 Docker가
  컨테이너 프로세스의 환경 변수로 직접 주입하므로 그대로 적용됩니다(아래 §3 예시).
- `HYPERCLOVA_*`는 `pydantic-settings`(`common/config.py`의 `HyperClovaSettings`)가
  `env_file=".env"`로 **직접 `.env` 파일을 읽도록 선언**되어 있어, 로컬 실행에서도 같은
  디렉터리의 `.env`에 적어 두면 자동으로 반영됩니다. Docker에서는 이미지에 `.env`를 넣지
  않으므로(`.dockerignore`) 컨테이너 환경 변수 또는 `--env-file`로 주입해야 합니다. 자격
  증명은 git에 커밋하지 않습니다. `.env.example`을 복사해 서버에서만 채웁니다. 셋 중
  하나라도 비어 있으면 연동이 비활성화될 뿐 서버는 정상 기동합니다(`is_configured`/
  `is_active`, `common/config.py`).

## 3. 코퍼스 volume mount

코퍼스는 이미지에 들어 있지 않으므로 호스트 디렉터리를 컨테이너에 read-only로 mount합니다.

```text
host:      /srv/disclosure-agent/corpus
container: /data/corpus
```

```bash
docker run -d \
  --name disclosure-agent \
  -p 8000:8000 \
  -v /srv/disclosure-agent/corpus:/data/corpus:ro \
  -e DISCLOSURE_CORPUS_ROOT=/data/corpus \
  -e DISCLOSURE_CORPUS_MANIFEST=/data/corpus/manifest.jsonl \
  --env-file /srv/disclosure-agent/.env \
  disclosure-agent:local
```

`--env-file`에는 `HYPERCLOVA_*` 값만 두고, 실제 값은 이 문서에도 git에도 적지 않습니다.

## 4. 확인

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

`/health`는 프로세스 liveness만 봅니다. 외부 LLM 호출·코퍼스 조회를 하지 않으므로 corpus
mount 여부와 무관하게 200을 반환합니다.

```bash
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "한미반도체 2025년 연결 기준 매출액이 얼마야?"}'
```

`/api/ask`는 `DISCLOSURE_CORPUS_ROOT`가 가리키는 코퍼스를 실제로 조회하므로, mount가 없거나
비어 있으면 답변 가능성 판정이 그 사실을 그대로 반영합니다(코퍼스 부재를 임의 값으로
메꾸지 않습니다).
