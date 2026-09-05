const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const appSource = fs.readFileSync(
  path.resolve(__dirname, "../../src/disclosure_agent/api/ui/app.js"),
  "utf8",
);

// 현재 app.js가 실제로 document.querySelector(All)로 조회하는 selector 전체다.
// 하나라도 빠지면 그 자리는 undefined가 되고, 스크립트 로드 시 또는 렌더 시
// "Cannot set properties of undefined"로 조용히 죽는다 — 값을 추측해 채우지 않고
// app.js 원문에서 그대로 옮겼다.
const SELECTORS = [
  "#question-form",
  "#question",
  "#results",
  "#result-card",
  "#form-message",
  "#state-card",
  ".state-icon",
  "#state-title",
  "#state-description",
  "#status-badge",
  "#reason-message",
  "#answer-summary",
  "#facts-section",
  "#facts-count",
  "#facts-grid",
  "#calculations-section",
  "#calculations-list",
  "#comparisons-section",
  "#comparisons-list",
  "#events-section",
  "#events-list",
  "#resolved-states-section",
  "#resolved-states-list",
  "#histories-section",
  "#histories-list",
  "#summaries-section",
  "#summaries-list",
  "#narratives-section",
  "#narratives-list",
  "#warnings-section",
  "#warnings-list",
  "#evidence-panel",
  "#evidence-count",
  "#evidence-list",
];

class FakeClassList {
  constructor() {
    this.values = new Set();
  }
  add(value) {
    this.values.add(value);
  }
  remove(value) {
    this.values.delete(value);
  }
  contains(value) {
    return this.values.has(value);
  }
}

class FakeElement {
  constructor() {
    this.attributes = {};
    this.classList = new FakeClassList();
    this.dataset = {};
    this.disabled = false;
    this.hidden = false;
    this.innerHTML = "";
    this.listeners = {};
    this.textContent = "";
    this.value = "";
  }

  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }
  focus() {}
  querySelector() {
    return this.submitButton;
  }
  scrollIntoView() {}
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
}

/**
 * app.js 한 벌을 격리된 vm context에 새로 로드한다.
 *
 * fetchImpl은 실제 네트워크/HCX/코퍼스를 전혀 부르지 않는 순수 함수다 — 이 스위트가
 * 검증하는 것은 서버 응답을 받은 뒤 UI가 어떻게 반응하는지이지, 서버 자체가 아니다.
 */
const createUi = (fetchImpl) => {
  const elements = Object.fromEntries(
    SELECTORS.map((selector) => [selector, new FakeElement()]),
  );
  elements["#question-form"].submitButton = new FakeElement();
  elements["#result-card"].hidden = true;
  elements["#state-card"].hidden = true;
  elements["#results"].hidden = true;

  const document = {
    querySelector: (selector) => elements[selector],
    querySelectorAll: () => [],
  };
  const context = { document, fetch: fetchImpl };
  vm.runInNewContext(appSource, context);

  return {
    elements,
    form: elements["#question-form"],
    input: elements["#question"],
    submitButton: elements["#question-form"].submitButton,
  };
};

const okResponse = (body) => ({
  ok: true,
  status: 200,
  json: async () => body,
});

const answerableBody = (overrides = {}) => ({
  answer: "테스트 답변입니다.",
  status: "ANSWERABLE",
  facts: [],
  events: [],
  event_links: [],
  event_histories: [],
  resolved_event_states: [],
  evidence: [],
  calculations: [],
  comparisons: [],
  document_summaries: [],
  narrative_comparisons: [],
  warnings: [],
  ...overrides,
});

// ---------------------------------------------------------------------------
// 1. 빈 질문 제출 방지 — fetch 자체가 불리지 않는다.
// ---------------------------------------------------------------------------

test("empty (or whitespace-only) question does not call the API", async () => {
  let fetchCalls = 0;
  const ui = createUi(async () => {
    fetchCalls += 1;
    throw new Error("fetch must not be called for an empty question");
  });
  ui.input.value = "   ";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(fetchCalls, 0);
  assert.equal(ui.elements["#results"].hidden, true);
});

// ---------------------------------------------------------------------------
// 2. 중복 submit 방지 — 요청이 끝나기 전에는 버튼이 비활성화된다.
// ---------------------------------------------------------------------------

test("the submit button is disabled synchronously while a request is in flight", async () => {
  let fetchCalls = 0;
  let resolveFetch;
  const pending = new Promise((resolve) => {
    resolveFetch = resolve;
  });
  const ui = createUi(() => {
    fetchCalls += 1;
    return pending;
  });
  ui.input.value = "삼성전자 2025년 매출 알려줘";

  const submitPromise = ui.form.listeners.submit({ preventDefault() {} });

  // async 함수는 첫 await(=fetch 호출)까지 동기 실행되므로, 이 시점에 이미
  // disabled=true여야 duplicate submit이 막힌다.
  assert.equal(ui.submitButton.disabled, true);

  resolveFetch(okResponse(answerableBody()));
  await submitPromise;

  assert.equal(fetchCalls, 1);
  assert.equal(ui.submitButton.disabled, false);
});

// ---------------------------------------------------------------------------
// 3. 요청 중 loading 상태가 실제로 보인다.
// ---------------------------------------------------------------------------

test("shows a loading state while waiting for the response", async () => {
  let resolveFetch;
  const pending = new Promise((resolve) => {
    resolveFetch = resolve;
  });
  const ui = createUi(() => pending);
  ui.input.value = "삼성전자 2025년 매출 알려줘";

  const submitPromise = ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(ui.elements["#results"].hidden, false);
  assert.equal(ui.elements["#state-card"].hidden, false);
  assert.equal(ui.elements["#result-card"].hidden, true);
  assert.equal(ui.elements["#state-title"].textContent, "공시를 분석하고 있습니다");

  resolveFetch(okResponse(answerableBody()));
  await submitPromise;

  assert.equal(ui.elements["#state-card"].hidden, true);
  assert.equal(ui.elements["#result-card"].hidden, false);
});

// ---------------------------------------------------------------------------
// 4. ANSWERABLE 정상 렌더링.
// ---------------------------------------------------------------------------

test("an ANSWERABLE response renders the answer and status", async () => {
  const ui = createUi(async () =>
    okResponse(answerableBody({ answer: "한미반도체 매출액은 1,000억원입니다." })),
  );
  ui.input.value = "한미반도체 2025년 매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(ui.elements["#result-card"].hidden, false);
  assert.equal(ui.elements["#result-card"].dataset.status, "ANSWERABLE");
  assert.equal(
    ui.elements["#answer-summary"].textContent,
    "한미반도체 매출액은 1,000억원입니다.",
  );
});

// ---------------------------------------------------------------------------
// 5/6. UNANSWERABLE/UNRESOLVED도 오류창이 아니라 정상 결과로 렌더링된다.
// ---------------------------------------------------------------------------

for (const [status, reason, answer] of [
  [
    "UNANSWERABLE",
    "out_of_data_range",
    "해당 기간의 공시 데이터를 보유하고 있지 않습니다.",
  ],
  [
    "UNRESOLVED",
    "ambiguous_match",
    "질문에 해당하는 대상을 하나로 확정하지 못했습니다.",
  ],
]) {
  test(`a ${status} response renders as a normal result, not an error state`, async () => {
    const ui = createUi(async () => okResponse(answerableBody({ status, reason, answer })));
    ui.input.value = "테스트 질문";

    await ui.form.listeners.submit({ preventDefault() {} });

    assert.equal(
      ui.elements["#result-card"].hidden,
      false,
      `${status} must render inside #result-card, not the #state-card error/blocked state`,
    );
    assert.equal(ui.elements["#state-card"].hidden, true);
    assert.equal(ui.elements["#result-card"].dataset.status, status);
    assert.equal(ui.elements["#answer-summary"].textContent, answer);
    assert.equal(ui.elements["#reason-message"].hidden, false);
  });
}

// ---------------------------------------------------------------------------
// 7. BLOCKED(422)와 5xx는 사용자 친화적 오류 상태로, raw 내용을 노출하지 않는다.
// ---------------------------------------------------------------------------

test("a 422 BLOCKED response shows a friendly blocked state, not the raw error body", async () => {
  const ui = createUi(async () => ({
    ok: false,
    status: 422,
    json: async () => ({
      execution_status: "BLOCKED",
      error: {
        stage: "query_plan",
        reason: "missing_year",
        message: "요청을 처리하는 데 필요한 조건을 충족하지 못했습니다.",
      },
    }),
  }));
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(ui.elements["#result-card"].hidden, true);
  assert.equal(ui.elements["#state-card"].hidden, false);
  assert.equal(
    ui.elements["#state-title"].textContent,
    "질문을 현재 조건으로 처리할 수 없습니다",
  );
  assert.equal(
    ui.elements["#state-description"].textContent,
    "요청을 처리하는 데 필요한 조건을 충족하지 못했습니다.",
  );
});

test("a 422 request-validation error (empty body) shows a friendly format message", async () => {
  const ui = createUi(async () => ({
    ok: false,
    status: 422,
    json: async () => ({
      detail: [{ type: "missing", loc: ["body", "question"], msg: "Field required" }],
    }),
  }));
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(ui.elements["#state-card"].hidden, false);
  assert.equal(ui.elements["#state-title"].textContent, "질문 형식을 확인해 주세요");
});

test("a 500 FAILED response shows a friendly error without leaking internals", async () => {
  const ui = createUi(async () => ({
    ok: false,
    status: 500,
    json: async () => ({
      execution_status: "FAILED",
      error: {
        stage: "runtime",
        reason: "unexpected_failure",
        message: "요청 처리 중 오류가 발생했습니다.",
      },
    }),
  }));
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(ui.elements["#result-card"].hidden, true);
  assert.equal(ui.elements["#state-card"].hidden, false);
  assert.equal(ui.elements["#state-title"].textContent, "요청 처리 중 오류가 발생했습니다");
  assert.equal(
    ui.elements["#state-description"].textContent,
    "요청 처리 중 오류가 발생했습니다.",
  );
});

test("a network failure (fetch rejects) shows a friendly connection error", async () => {
  const ui = createUi(async () => {
    throw new TypeError("Failed to fetch");
  });
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(ui.elements["#result-card"].hidden, true);
  assert.equal(ui.elements["#state-card"].hidden, false);
  assert.equal(ui.elements["#state-title"].textContent, "공시 Agent에 연결할 수 없습니다");
  // stack trace/원문 오류 메시지가 그대로 새지 않는다.
  assert.equal(
    ui.elements["#state-description"].textContent.includes("TypeError"),
    false,
  );
});

// ---------------------------------------------------------------------------
// 8. evidence가 응답에 있으면 표시된다.
// ---------------------------------------------------------------------------

test("evidence already present in the response is rendered", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        evidence: [
          {
            evidence_id: "synthetic-evidence-1",
            doc_id: "synthetic-doc-1",
            location_type: "table",
            section_path: ["요약재무정보"],
            raw_text: "매출액 100,000,000원",
          },
        ],
      }),
    ),
  );
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(ui.elements["#evidence-panel"].hidden, false);
  assert.equal(ui.elements["#evidence-count"].textContent, "1개 근거");
  assert.match(ui.elements["#evidence-list"].innerHTML, /synthetic-doc-1/);
  assert.match(ui.elements["#evidence-list"].innerHTML, /매출액 100,000,000원/);
});

test("no evidence in the response means the evidence panel stays hidden", async () => {
  const ui = createUi(async () => okResponse(answerableBody({ evidence: [] })));
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(ui.elements["#evidence-panel"].hidden, true);
});

// ---------------------------------------------------------------------------
// 9. UI가 organizer용 GET /answer가 아니라 production POST /api/ask만 부른다.
// ---------------------------------------------------------------------------

test("the UI calls POST /api/ask and never the organizer GET /answer endpoint", async () => {
  let calledUrl = null;
  let calledOptions = null;
  const ui = createUi(async (url, options) => {
    calledUrl = url;
    calledOptions = options;
    return okResponse(answerableBody());
  });
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(calledUrl, "/api/ask");
  assert.equal(calledOptions.method, "POST");
  assert.equal(JSON.parse(calledOptions.body).question, "매출 알려줘");

  // 소스 자체에도 organizer 경로 리터럴이 없어야 한다("#answer-summary"·
  // "response.answer"처럼 슬래시 없이 "answer"를 포함하는 식별자와는 구분된다).
  assert.equal(appSource.includes("/answer"), false);
});
