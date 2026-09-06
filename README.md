README(6).md
파일
어때 다 수정된 리드미 파일 보내줄게. 제출하기에 적절해보여??

응. 평가용 API라면 /answer가 맞아. README에서도 일반 사용자용 API와 평가용 API를 따로 두고 있어.

즉 우리 서버 IP가 지금 쓰는 34.64.175.191이라면 최종 README에는:

- **Endpoint:** `http://34.64.175.191/answer`

이렇게 쓰는 구조가 맞아.

헷갈릴 수 있는 게 두 개가 따로 있기 때문이야.

용도	Endpoint
웹 UI / 우리 서비스 내부 질의	POST /api/ask
공모전 평가용 API	GET /answer

그러니까 /answer를 /api/ask로 바꾸면 안 돼. 평가 규격에 맞추기 위해 /answer adapter를 따로 둔 거야.

다만 README를 확정하기 전에 딱 한 번 실제 서버에서 확인하자:

curl -i -G "http://34.64.175.191/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=한미반도체 2025년 연결 기준 매출액이 얼마야?"

여기서 HTTP 200이 뜨고,

{
  "question_id": "...",
  "question": "...",
  "retrieved_context": "...",
  "think_trace": "...",
  "answer": "..."
}

형태가 나오면 README의 최종 Endpoint는 http://34.64.175.191/answer로 확정하면 돼.

개념 한 문장: /api/ask는 서비스용 질의 API이고, /answer는 공모전 평가 규격에 맞춘 별도 GET endpoint다.

예상되는 다음 질문 3개:

위 curl 결과 보내면 정상인지 봐줄까?
/answer가 404면 서버에서 뭘 확인해야 할까?
실제 주소 반영해서 README 커밋·푸시까지 이어서 할까?
팀원3의 이번 readme의 pr 에 대한 피드백:
그리고 **있으면 더 좋은 수정 3개**가 있어.

첫째, UI도 실제 외부 배포돼 있으니까 설치/실행 설명과 별개로 API 사용 근처에 한 줄 넣어도 좋아:


md
배포된 웹 UI: `http://49.50.143.186/`

다만 공모전 제출 요구사항은 /answer니까 이건 선택사항이야.

둘째, /answer의 BLOCKED 동작을 한 줄 적어두면 구현과 문서가 더 정확히 맞아:

일반 `/api/ask`에서 BLOCKED인 요청은 HTTP 422이지만,
평가용 `/answer` adapter에서는 평가 응답 contract를 유지하기 위해
HTTP 200의 5-field 응답으로 변환합니다. FAILED 및 예기치 않은 예외는 5xx로 반환합니다.

이건 우리가 만든 organizer adapter의 실제 semantics와 맞아.

셋째, README에 지금 분기·반기 단건 수치 지원이 꽤 명확하게 적혀 있어:

1분기보고서 → 3M YTD
반기보고서 → 6M YTD
3분기보고서 → 9M YTD

그리고 아래 제한사항에는:

interim 단건 조회는 누적 매출액이 대상
interim 비교와 문서 요약은 지원하지 않음

이라고 되어 있어서 문서 내부적으로는 서로 모순되지 않아. 다만 이 부분은 최종 main에 해당 기능이 실제 merge돼 있는지만 제출 전에 확인하면 돼. 아직 team2 branch에만 있는 기능이면 README가 코드보다 앞서가는 문제가 생길 수 있으니까.

팀원3 피드백은 전부 타당해. 나는 1번·2번은 README에 반영하고, 3번은 코드 상태 확인 후 유지 여부를 결정하는 쪽을 추천해.

1. 배포 웹 UI 주소 — 넣는 게 좋음

필수는 아니지만 심사위원 입장에서는 repo를 보고 바로 실제 서비스를 열어볼 수 있어서 꽤 유용해.

## API 사용 시작 부분에 이 정도만 추가하면 충분해.

배포된 웹 UI: `http://49.50.143.186/`

굳이 별도 ## Demo 섹션까지 만들 필요는 없어.

2. /answer의 BLOCKED 처리 — 넣는 게 좋음

이건 단순 부가설명이 아니라 /api/ask와 /answer의 HTTP semantics가 다르다는 중요한 차이라서 넣는 편이 정확해.

현재 README에는 일반 API에 대해:

BLOCKED → HTTP 422
FAILED → HTTP 500

이라고 되어 있는데, 바로 뒤 Evaluation API는 별도 adapter라면 아래 설명을 추가하는 게 좋아.

일반 `/api/ask`에서 `BLOCKED`인 요청은 HTTP 422로 반환되지만,
평가용 `/answer` adapter에서는 평가 응답 contract를 유지하기 위해
HTTP 200의 5-field 응답으로 변환합니다.
`FAILED` 및 예기치 않은 서버 오류는 5xx로 반환합니다.

이걸 ## Evaluation API의 JSON 응답 예시 바로 아래에 넣으면 가장 자연스러워.

3. 분기·반기 지원 — 이건 진짜 확인하고 가야 함

이 피드백이 제일 중요해.

README에는 지금 명확하게:

1분기 → 3M YTD
반기 → 6M YTD
3분기 → 9M YTD

라고 지원 기능으로 적혀 있어.

따라서 최종 제출 branch에 해당 수정이 아직 merge되지 않았다면 README가 실제 코드보다 앞서가는 상태가 돼. 이건 피해야 해.

현재 작업 브랜치가 README branch라면 우선 최신 develop 기준으로 확인하는 게 좋아.

git fetch origin

git log origin/develop --oneline --all -- \
  src/disclosure_agent/agent/handlers/single_fact.py

좀 더 직접적으로는:

git show origin/develop:src/disclosure_agent/agent/handlers/single_fact.py \
  | grep -n "fiscal_base_month\|base_month"

그리고 관련 테스트가 develop에 들어왔는지도:

git ls-tree -r --name-only origin/develop \
  | grep "periodic_period_selection"

테스트 파일이 있다면:

git show origin/develop:tests/unit/test_periodic_period_selection.py \
  | grep -n "quarter\|half\|base_month"

최종적으로 1분기/반기/3분기 테스트가 origin/develop에서 존재하고 handler propagation까지 들어가 있으면 README 그대로 유지하면 돼.

반대로 team2 또는 다른 feature branch에만 있다면 두 선택지가 있어.

해당 기능 PR을 먼저 develop에 merge → README 유지
아직 merge하지 않을 거라면 → README에서 interim 지원 설명 삭제/완화

나는 당연히 기능 PR을 먼저 최종 develop에 넣고 README는 유지하는 게 좋아 보여. 이미 구현·테스트까지 해둔 기능을 최종 제출 README에서 뺄 이유는 없으니까.

그래서 지금 순서는 이렇게 하면 가장 안전해.

① develop에 interim 기능 merge 여부 확인
        ↓
② 문제 없으면 README 그대로 유지
        ↓
③ 웹 UI 주소 추가
        ↓
④ /answer BLOCKED semantics 추가
        ↓
⑤ 실제 /answer Endpoint 주소 반영
        ↓
⑥ 최종 README commit

Codex에는 아주 짧게 이렇게 맡기면 돼.

README.md만 수정해 주세요. 다른 파일은 절대 수정하지 마세요.

API 사용 섹션 근처에 배포된 웹 UI: http://49.50.143.186/ 한 줄을 추가합니다.
Evaluation API 섹션의 response 설명 다음에 다음 semantics를 추가합니다: 일반 /api/ask에서 BLOCKED 요청은 HTTP 422이지만, 평가용 /answer adapter에서는 5-field 평가 응답 contract를 유지하기 위해 HTTP 200으로 변환하며, FAILED 및 예기치 않은 예외는 5xx로 반환합니다.
분기·반기 단건 수치 지원 설명은 수정하지 마세요.
README 외 파일은 수정하지 말고 git diff --check 및 git status --short 결과를 보고해 주세요.

단, 3번 interim merge 여부는 Codex에게 README 수정시키기 전에 우리가 먼저 확인하는 게 제일 안전해.

핵심 개념 한 문장: 일반 /api/ask와 평가용 /answer는 같은 Agent를 사용하더라도 평가 contract를 유지하기 위해 HTTP 응답 정책이 다를 수 있다.

다음으로 예상되는 질문 3개
develop에 interim 기능이 실제 merge됐는지 내가 확인할 명령을 하나씩 같이 실행해줄까?
위 두 README 수정까지 반영한 뒤 다시 커밋·푸시 순서 알려줄까?
마지막으로 /answer 실제 endpoint를 서버에서 직접 테스트해볼까?
그럼 이부분 다 수정하고 보완한 readme 파일 보내줄래??

수정·보완한 최종 README 파일이야.

최종 제출용 README 다운로드

반영한 내용은 배포 웹 UI http://49.50.143.186/ 추가, Evaluation API를 http://34.64.175.191/answer로 반영, /answer에서 BLOCKED는 HTTP 200의 5-field contract로 변환되고 FAILED/예외는 5xx라는 설명 추가야. 분기·반기 단건 조회 설명은 팀원3 피드백대로 그대로 유지했어.

단, 최종 제출 직전에 http://34.64.175.191/answer가 실제로 외부에서 호출되는지만 반드시 확인하고 그대로 올리면 돼.

다음으로 확인할 만한 것 3개:

/answer를 실제 서버에 curl로 호출해서 최종 검증할까?
이 파일과 기존 README의 변경점만 diff 형태로 보여줄까?
README 확정 후 커밋·푸시 명령까지 바로 이어서 할까? 
README_final_submission.md
문서

라이브러리
/
ai festival
/
README_final_submission.md
DART 공시 AI Agent

금융감독원 DART 공시를 기반으로 재무 수치와 기업 활동을 조회하고, 비교·계산 결과를 원문 근거와 함께 제공하는 질의응답 시스템입니다. 정기공시, 공급계약·신규시설투자 등 거래소공시, 대량보유 보고서를 처리합니다. XML의 문서·Section·표 구조를 보존해 사실을 추출하고, 수치 계산과 정정공시의 변경 추적은 Python 코드로 수행합니다. HyperCLOVA X는 확정된 사실을 자연어로 표현하며, 문서나 근거를 확정할 수 없는 요청에는 임의의 값을 만들어 답하지 않습니다.

해결 방법과 시스템 흐름

공시의 숫자는 단위, 연결/별도 기준, 보고 기간, 정정 여부에 따라 의미가 달라집니다. 이 프로젝트는 XML을 단순 텍스트 조각으로만 다루지 않고, 값의 의미와 원문 위치를 함께 보존하는 구조화 처리를 중심으로 설계했습니다.

flowchart TD
    Q["질문: 웹 UI / API"] --> G["안전성·외부 데이터 범위 검사"]
    G --> QA["manifest 기업·기간 정보 + QueryPlan"]
    QA --> H["intent별 Handler"]
    H --> F["manifest 필터·문서 선택"]
    F --> P["XML Parser → Extractor → Adapter"]
    P --> S["Fact / Section 근거 / Event"]
    S --> C["필요 시 Python 계산·비교"]
    S --> E["이벤트 정정 연결·필드 변경·최신 유효 상태"]
    S --> R["AgentExecutionResult: 결과·근거·답변 가능성"]
    C --> R
    E --> R
    R --> A["결정론적 답변 구성"]
    R --> L["선택적 GroundedPayload → HyperCLOVA X → 검증"]
    A --> O["AnswerResponse + Evidence + SourceDocument"]
    L -->|"검증 통과 시 answer 문장 교체"| O
    G -->|"범위·정책상 답변 불가"| R

문서 선택은 retrieval/metadata_filter.py, 실행 경로 선택은 agent/router.py의 Handler 등록표가 담당합니다. 질의 해석, 원문 추출, 계산, 표현 단계의 책임을 구분하며, 지원하지 않는 요청을 다른 유형의 질문으로 바꿔 처리하지 않습니다.

핵심 기술
XML 구조와 Evidence 보존

정기공시 Parser는 metadata, sections, table_groups, tables를 생성합니다. 셀의 ACODE, ACONTEXT, ROWSPAN, COLSPAN을 보존하고, Extractor는 병합 헤더와 단위 선언을 함께 읽어 수치의 기간·회계기준을 판정합니다.

XML → Section 계층 → Table / 행 / 열 → 원문 값·단위·기간 → Fact + Evidence

실행 계약은 common/schemas.py에 정의되어 있습니다.

계약	역할
Fact	raw_value·raw_unit과 정규화된 값·단위를 분리하고, 기간·회계기준·evidence_ids를 기록
Evidence	doc_id, section_path, 행·열 label, 원문 발췌와 근거 위치를 연결
EvidenceProvenance	제공 가능한 표·행·열 인덱스, ACODE/ACONTEXT, 단위 선언·배수·신뢰 여부를 보존
SourceDocument	실제 선택한 문서의 보고서명·회계연도·종료월·접수번호·공시일을 Evidence.doc_id로 연결
RawReference	정정공시에 적힌 원공시일·정정일·정정사유·관련 공시 표기를 연결 전 상태로 보존

근거 필드의 채움 범위는 문서와 추출 경로에 따라 다릅니다. 원문에 없는 metadata를 질문이나 식별자에서 추정해 채우지 않습니다.

정기공시 조회·계산
단건 수치 조회: 사업보고서의 매출액·영업이익, 분기·반기보고서의 누적 매출액. 연결/별도 기준과 원문 단위를 확인합니다.
연간 비교: 기업 간 매출액·영업이익률 비교, 연도 간 매출 증감액·증감률과 영업이익률 변화 계산. 입력의 단위·기간·회계기준이 비교 가능한지 확인합니다.
사업내용 조회: 사업의 개요·주력 제품·설비투자 등 지원되는 Section 범위의 내용과 원문 근거를 제공합니다.
결정론적 계산: 원천 Fact와 계산 결과를 구분하고, Decimal 기반 Python 계산을 사용합니다. LLM이 숫자를 직접 계산하지 않습니다.

명시적 보고서 요청의 기간 계약은 다음과 같습니다. 두 분기보고서는 새 subtype 없이 종료월로 구분합니다.

요청	document_subtype	base_month	조회 기간
1분기보고서	quarter	3	1~3월 누적, 3M YTD
반기보고서	half	6	1~6월 누적, 6M YTD
3분기보고서	quarter	9	1~9월 누적, 9M YTD
사업보고서	annual	12	연간 FY

QueryPlan.fiscal_base_month는 단건 조회 Handler를 거쳐 locator의 base_month로 전달됩니다. 요청한 월의 문서가 없으면 다른 분기 문서를 대신 선택하지 않습니다. 상대 연간 기간은 시스템 날짜가 아니라 기업별 corpus의 최신 사업보고서 회계연도를 기준으로 해석합니다.

이벤트·정정공시 추적

공급계약의 금액·기간 조회, 신규시설투자 내용 조회, 지원되는 시설투자 합산·기업 비교 및 정정 이력을 처리합니다. 대량보유 보고서의 보유주식 수·보유비율 조회도 별도 holding 경로로 제공합니다.

원공시 → 정정공시 → 추가 정정공시
       → EventLink / EventHistory / FieldChange
       → ResolvedEventState: 필드별 최신값 + 해당 값의 문서·Evidence

event/linker.py의 build_filing_chain이 문서 관계를 구성하고, diff_correction이 정정 필드를 해석합니다. resolve_latest_event_state는 완결된 체인에 명시된 변경을 적용하며, resolve_original_event_state는 원공시 당시 상태를 제공합니다. 따라서 최신 유효 상태의 각 필드는 서로 다른 문서를 근거로 가질 수 있습니다. 연결 후보가 모호하거나 체인이 불완전하면 최신값을 확정하지 않습니다.

Fail-closed와 답변 가능성

답변 가능성과 실행 상태는 별개의 계약입니다.

AnswerResponse.status	의미
ANSWERABLE	확보한 근거로 답변 가능
PARTIALLY_ANSWERABLE	일부만 근거로 뒷받침 가능
UNANSWERABLE	데이터·정책·비교 가능성 등의 이유로 답변 불가
UNRESOLVED	문서 후보나 연결 관계를 하나로 확정하지 못함

내부 AgentExecutionResult.execution_status는 COMPLETED, BLOCKED, FAILED를 사용합니다. 정상 실행도 답변 불가로 끝날 수 있습니다. 문서·기간·필수 근거·정정 관계를 확정하지 못하면 기존 사유 코드로 종료하며, 첫 후보나 비슷한 기간의 값으로 대체하지 않습니다.

HyperCLOVA X와 결정론적 fallback

HyperCLOVA X에는 raw XML 전체가 아니라 추출·계산을 마친 GroundedPayload를 전달합니다. verbalizer.validate_answer는 모델 출력의 JSON 형식, 참조 ID, 숫자, 기업·기간·지표 결합, 정정 이력 의미와 근거 밖 주장을 검사합니다.

설정 누락, 호출 실패, 출력 검증 실패 시 이미 구성된 결정론적 답변을 사용합니다. 모델 표현이 채택되어도 answer 문장만 교체하며 Fact·Evidence·답변 가능성 상태는 바꾸지 않습니다. 외부 LLM 없이도 구조화 조회와 답변이 동작합니다.

프로젝트 구조
src/disclosure_agent/
├── common/       # Pydantic 계약, enum, 설정·날짜 처리
├── periodic/     # 정기공시 XML 파싱, 재무 추출, Section 선택, adapter
├── event/        # 거래소공시 파싱·추출, 정정 연결·차이·유효 상태
├── holding/      # 대량보유 보고서 파싱·추출
├── retrieval/    # manifest 필터, 문서 후보 조회·경로 해석
├── agent/        # QueryPlan, intent별 handler, 계산·근거·안전성·LLM 표현
└── api/          # FastAPI, 주최측 adapter, 정적 웹 UI
evaluation/      # 대표 질문·gold, 평가 runner, 별도 generalization 평가셋
tests/           # unit, integration, evaluation, 합성 fixture
scripts/         # 설치·서버·테스트 실행, corpus 회귀·감사 도구
docs/            # 설계 결정, XML 분석, 평가·배포 문서
data/            # 로컬 corpus와 가공 결과 위치

현재 조회의 중심은 manifest 필터와 문서 구조 탐색입니다. retrieval/keyword.py, vector.py, fusion.py는 인터페이스만 있으며, 범용 keyword/vector 검색이나 hybrid RAG를 구현한 시스템으로 간주하지 않습니다.

설치와 실행

Python 3.12와 uv, 별도로 준비한 DART corpus가 필요합니다. 저장소 루트에서 실행합니다.

uv sync
cp .env.example .env

# 아래 데이터 구조에 맞춰 별도 corpus를 배치한 뒤 실행
export DISCLOSURE_CORPUS_ROOT=./data/raw/corpus
uv run uvicorn disclosure_agent.api.main:app --host 0.0.0.0 --port 8000

브라우저에서 http://127.0.0.1:8000/을 열면 웹 UI를 사용할 수 있습니다. UI는 같은 FastAPI가 HTML/JavaScript/CSS를 제공하므로 별도 프런트엔드 빌드·서버가 필요하지 않습니다. /docs에서 OpenAPI 문서를 확인할 수 있습니다.

macOS/Linux용 scripts/bootstrap.sh, run_api.sh, run_tests.sh와 Windows용 대응 .ps1 스크립트도 제공합니다. Windows에서는 corpus 경로를 $env:DISCLOSURE_CORPUS_ROOT로 지정할 수 있습니다.

데이터 배치

대용량 DART 원문 corpus는 저장소와 Docker 이미지에 포함하지 않습니다. API를 기동하는 것만으로 데이터를 내려받지 않으므로 별도로 준비해야 합니다.

data/raw/corpus/
├── manifest.jsonl
└── raw/
    ├── periodic/<기업>/<공시 디렉터리>/*.xml
    ├── exchange/<기업>/<공시 디렉터리>/*.xml
    └── holding/<기업>/<공시 디렉터리>/*.xml

실제 파일 위치는 manifest의 file_path로 해석합니다. 기업명, 문서군·subtype, 회계연도·종료월, 접수번호·공시일, 정정 여부가 문서 선택에 사용됩니다. 위 경로는 배치 예시이며 corpus 루트와 manifest 위치는 환경 변수로 바꿀 수 있습니다. 실제 원문·credential·로컬 생성 결과를 커밋하지 마세요.

환경 변수
변수	용도
DISCLOSURE_CORPUS_ROOT	실제 corpus 루트. 미지정 시 DATA_ROOT, 그마저 없으면 ./data 사용
DISCLOSURE_CORPUS_MANIFEST	별도 manifest 경로. 기본값은 corpus 루트의 manifest.jsonl
HYPERCLOVA_API_KEY	HyperCLOVA X 인증 키
HYPERCLOVA_API_URL	모델 경로를 포함하거나 모델명을 붙일 수 있는 chat-completions URL
HYPERCLOVA_MODEL	모델명. .env.example의 기본값은 HCX-007
HYPERCLOVA_ENABLED	false로 지정하면 결정론적 표현만 사용
HYPERCLOVA_TIMEOUT_SECONDS, HYPERCLOVA_MAX_RETRIES, HYPERCLOVA_MAX_TOKENS	선택적 호출 시간·재시도·출력 길이 설정

로컬 실행에서 corpus 변수는 프로세스 환경 변수로 설정해야 합니다. .env에 적는 것만으로는 적용되지 않습니다. 반면 HYPERCLOVA_*는 HyperClovaSettings가 .env에서도 읽으며, 키·URL·모델명이 모두 채워져야 연동이 활성화됩니다. 실제 키는 .env.example이나 README에 기록하지 않습니다.

Docker

저장소 루트의 data/raw/corpus에 원문을 준비한 경우:

docker build -t disclosure-agent:local .
docker run --rm -p 8000:8000 \
  --mount type=bind,source="$(pwd)/data/raw/corpus",target=/data/corpus,readonly \
  -e DISCLOSURE_CORPUS_ROOT=/data/corpus \
  --env-file .env \
  disclosure-agent:local

Docker는 uv.lock 기준으로 실행 의존성을 설치하고, corpus는 읽기 전용으로 mount합니다. 상세 배포 방법은 docs/deployment.md를 참고하세요.

API 사용

배포된 웹 UI: http://49.50.143.186/

curl -X POST http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"한미반도체 2025년 연결 기준 매출액이 얼마야?"}'

POST /api/ask의 요청은 AskRequest(question: str)이며, 정상 실행의 응답은 다음 필드를 갖는 AnswerResponse입니다.

필드	내용
answer, status, reason	답변 문장, 답변 가능성, 필요한 경우 사유
facts, evidence, source_documents	원천 수치와 셀·문서 근거
events, event_links, event_histories, resolved_event_states	이벤트, 정정 연결·변경 이력·유효 상태

결과 종류에 따라 사용하지 않는 목록은 비어 있습니다. 계산·비교 객체는 내부 AgentExecutionResult에 있으며, 현재 AnswerResponse에 별도 목록으로 노출되지 않습니다. 정상적으로 판단한 답변 불가도 HTTP 200이고, 실행 자체가 BLOCKED이면 HTTP 422, FAILED이면 HTTP 500의 execution_status·error 응답을 사용합니다.

엔드포인트	역할
GET /	웹 질문 UI
GET /health	프로세스 생존 확인: {"status":"ok"}. corpus·LLM 가용성 검사는 수행하지 않음
POST /api/ask	구조화 질의응답 API
GET /answer?question_id=...&question=...	주최측 평가 규격 adapter
Evaluation API

공모전 평가용 API는 일반 서비스 API와 같은 Agent 서비스를 재사용합니다.

Endpoint: http://34.64.175.191/answer
요청: GET /answer?question_id={id}&question={query}
Query parameters: question_id는 질문 식별자, question은 URL 인코딩한 질문 문자열입니다.

응답은 다음 5개 문자열 필드로 구성됩니다.

{
  "question_id": "{id}",
  "question": "{query}",
  "retrieved_context": "원문에서 확보한 근거",
  "think_trace": "확정된 실행 상태와 처리 요약",
  "answer": "질문에 대한 답변"
}

think_trace는 확정된 실행 상태·결과 건수 등 처리 요약이며, 자유로운 추론 과정을 생성하거나 노출하지 않습니다.

일반 /api/ask에서 BLOCKED인 요청은 HTTP 422로 반환되지만, 평가용 /answer adapter에서는 평가 응답 contract를 유지하기 위해 HTTP 200의 5-field 응답으로 변환합니다. FAILED 및 예기치 않은 서버 오류는 5xx로 반환합니다.

지원 질문 예시

아래는 대표 평가 질문과 corpus integration 테스트에서 검증하는 조회 형태입니다. 실제 답변 가능 여부는 준비한 corpus의 문서·근거에 따라 달라집니다.

유형	질문
연간 수치	한미반도체 2025년 연결 기준 매출액이 얼마야?
3분기 누적 수치	삼성전자 2025년 3분기보고서 연결 매출액 알려줘
사업내용	한미반도체는 무슨 사업을 하고 주력 제품이 뭐야?
연간 매출 증감	한미반도체 매출이 작년보다 얼마나 늘었어?
영업이익률 변화	한미반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?
기업 간 비교	2025년 HD현대중공업이랑 삼성중공업 매출 비교해줘.
시설투자 합산 비교	2024년에 LG이노텍하고 레인보우로보틱스가 새로 발표한 시설투자, 회사별로 다 합치면 어디가 더 커?
공급계약	한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약기간은?
투자공시 내용	LG유플러스 파주 AIDC 투자 공시 내용 요약해줘.
정정 이력	레인보우로보틱스 사옥·제조시설 신축 투자, 그동안 계획이 몇 번 바뀌었어?
테스트와 평가
uv run pytest
uv run ruff check .

# 테스트 계층별 실행
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/evaluation -q

# 질문·gold 계약 검증: Agent를 실행하지 않음
uv run python -m evaluation.runner

# 실제 corpus로 Agent 실행 및 구조화 결과 평가
uv run python -m evaluation.runner --run-agent --corpus-root ./data/raw/corpus

일반 테스트는 합성 fixture를 사용하며, corpus 표시 테스트는 별도 로컬 원문이 필요하고 데이터가 없으면 건너뜁니다. 대표 평가에는 성공뿐 아니라 문서 모호성·데이터 부족·정책상 답변 불가도 포함됩니다. evaluation/generalization/은 기본 평가셋과 분리되어 있으며 명시적으로 경로를 지정해 사용합니다. 평가 방식과 추가 옵션은 evaluation/README.md, corpus 회귀 실행은 scripts/run_corpus_regression.py를 참고하세요.

현재 한계
공시 밖 주가·시장 반응 등 외부 시장 데이터, 근거 없는 미래 예측, 매수·매도 판단은 지원하지 않습니다.
준비한 corpus의 기간·문서·근거 범위 안에서만 답합니다. 문서나 정정 연결이 모호하면 사용자가 대상을 더 구체화해야 합니다.
interim 단건 조회는 누적 매출액이 대상입니다. 단일 2Q/3Q 3개월 값, interim 기간·기업 비교와 interim 문서 요약은 지원하지 않습니다.
연도별 사업내용의 자유로운 서술 변화 비교, 임의 문서 전체 요약, 지원 목록 밖 지표·복합 질문은 처리 범위가 제한됩니다.
모든 DART XML 변형과 정정 양식을 포괄하는 범용 Parser는 아닙니다. 기간·단위·필드 의미를 안전하게 확정하지 못하면 추출이나 최신 상태 확정을 중단합니다.
정기공시의 정정 판본 선택은 Handler별 기존 정책을 따릅니다. 이벤트 체인의 최신 유효 상태 처리를 모든 사업보고서 조회에 일반화하지 않습니다.
