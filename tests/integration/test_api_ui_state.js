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
  "#state-suggestions",
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

// app.js가 실제로 브라우저 DOM에서 하는 것처럼, innerHTML에 심어진
// data-question-fill 버튼을 querySelectorAll("[data-question-fill]")로 다시 꺼내
// 쓸 수 있어야 한다(#state-suggestions의 pill 버튼 클릭 동작 테스트에 필요). 그래서
// innerHTML setter에서 <button data-question-fill="...">...</button> 패턴만 최소한
// 파싱해 자식 FakeElement를 만든다 — 완전한 HTML 파서가 아니라 app.js가 실제로
// 만드는 마크업 모양 하나만 인식한다.
const unescapeHtml = (value = "") =>
  String(value)
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"');

class FakeElement {
  constructor() {
    this.attributes = {};
    this.classList = new FakeClassList();
    this.dataset = {};
    this.disabled = false;
    this.focused = false;
    this.hidden = false;
    this._innerHTML = "";
    this._children = [];
    this.listeners = {};
    this.textContent = "";
    this.value = "";
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(html) {
    this._innerHTML = html;
    this._children = [];

    const buttonPattern =
      /<button[^>]*data-question-fill="([^"]*)"[^>]*>([\s\S]*?)<\/button>/g;

    for (const match of html.matchAll(buttonPattern)) {
      const button = new FakeElement();
      button.dataset.questionFill = unescapeHtml(match[1]);
      button.textContent = unescapeHtml(match[2]).trim();
      button.click = () => button.listeners.click?.();
      this._children.push(button);
    }
  }

  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }
  focus() {
    this.focused = true;
  }
  querySelector() {
    return this.submitButton;
  }
  querySelectorAll(selector) {
    if (selector === "[data-question-fill]") {
      return this._children;
    }
    return [];
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
    context,
    form: elements["#question-form"],
    input: elements["#question"],
    submitButton: elements["#question-form"].submitButton,
  };
};

// app.js 안의 순수 함수(formatPercent, formatMoneyKorean, documentLabel 등)를 DOM
// 이벤트 없이 직접 호출한다. vm 컨텍스트는 하나의 전역 렉시컬 환경을 공유하므로,
// 한 번 로드된 top-level const/함수는 이후 runInContext 호출에서도 그대로 보인다.
// data는 호출 전에 context의 전역 프로퍼티로 주입해 코드 문자열 안에서 참조한다.
const evalInContext = (context, code, data = {}) => {
  Object.assign(context, data);
  return vm.runInContext(code, context);
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
// 6b. PARTIALLY_ANSWERABLE도 UNANSWERABLE/UNRESOLVED와 동일하게 오류 상태가
//     아니라 정상 결과로 렌더링되고, status/reason 문구가 production 정본
//     (STATUS_META/REASON_LABELS)과 정확히 일치해야 한다.
// ---------------------------------------------------------------------------

test("a PARTIALLY_ANSWERABLE response renders as a normal result with the correct status badge and reason text, not an error state", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        status: "PARTIALLY_ANSWERABLE",
        reason: "partial_source_support",
        answer: "확인된 근거 범위에서 일부 답변합니다.",
      }),
    ),
  );
  ui.input.value = "테스트 질문";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(
    ui.elements["#state-card"].hidden,
    true,
    "PARTIALLY_ANSWERABLE must not render the #state-card error/blocked state",
  );
  assert.equal(
    ui.elements["#result-card"].hidden,
    false,
    "PARTIALLY_ANSWERABLE must render inside #result-card",
  );
  assert.equal(
    ui.elements["#result-card"].dataset.status,
    "PARTIALLY_ANSWERABLE",
  );
  // app.js의 STATUS_META.PARTIALLY_ANSWERABLE.label 정본과 정확히 일치해야 한다.
  assert.equal(ui.elements["#status-badge"].textContent, "일부 답변 가능");
  // app.js의 REASON_LABELS.partial_source_support 정본과 정확히 일치해야 한다.
  assert.equal(
    ui.elements["#reason-message"].textContent,
    "질문의 일부 항목에 대해서만 공시 근거를 확보했습니다.",
  );
  assert.equal(ui.elements["#reason-message"].hidden, false);
  assert.equal(
    ui.elements["#answer-summary"].textContent,
    "확인된 근거 범위에서 일부 답변합니다.",
  );
});

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
    "조회 기준을 선택해 주세요",
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

test("evidence already present in the response is rendered, without leaking the raw doc_id", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        evidence: [
          {
            evidence_id: "synthetic-evidence-1",
            doc_id: "periodic_SYNTHETIC0001",
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
  assert.match(ui.elements["#evidence-list"].innerHTML, /매출액 100,000,000원/);

  // 이 evidence는 어떤 fact/document_summaries에도 연결되지 않았으므로(회사/연도
  // 정보 없음), 원시 doc_id("periodic_SYNTHETIC0001")를 그대로 보여주는 대신
  // "관련 공시 문서" 폴백 라벨을 써야 한다.
  assert.equal(
    ui.elements["#evidence-list"].innerHTML.includes("periodic_SYNTHETIC0001"),
    false,
  );
  assert.match(ui.elements["#evidence-list"].innerHTML, /관련 공시 문서/);
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

// ---------------------------------------------------------------------------
// 10. formatPercent — "%" 단위 값을 소수점 둘째 자리로 고정 표기한다.
// ---------------------------------------------------------------------------

test("formatPercent fixes values to two decimal places", () => {
  const ui = createUi(async () => okResponse(answerableBody()));

  assert.equal(evalInContext(ui.context, "formatPercent(12.3)"), "12.30");
  assert.equal(evalInContext(ui.context, "formatPercent(12)"), "12.00");
  assert.equal(evalInContext(ui.context, "formatPercent(12.345)"), "12.35");
});

// ---------------------------------------------------------------------------
// 11. formatMoneyKorean — "약 N조 N억" 보조 표기의 조/억 분해.
// ---------------------------------------------------------------------------

test("formatMoneyKorean splits an amount into 조/억, omitting empty units", () => {
  const ui = createUi(async () => okResponse(answerableBody()));

  // 조와 억이 모두 존재.
  assert.equal(
    evalInContext(ui.context, "formatMoneyKorean(17580633257000)"),
    "17조 5,806억",
  );

  // 억만 존재(조 단위 없음) — "0조"를 붙이지 않는다.
  assert.equal(
    evalInContext(ui.context, "formatMoneyKorean(12345678901)"),
    "123억",
  );

  // 1억 미만은 보조 표기가 의미 없으므로 null(호출부는 이때 보조 줄을 붙이지 않는다).
  assert.equal(evalInContext(ui.context, "formatMoneyKorean(50000000)"), null);

  // 음수는 부호를 앞에 붙이고 절댓값을 조/억으로 분해한다.
  assert.equal(
    evalInContext(ui.context, "formatMoneyKorean(-1234567890123)"),
    "-1조 2,345억",
  );
});

// ---------------------------------------------------------------------------
// 12. documentLabel — report_name > company+fiscal_year(+fiscal_period) > 폴백.
// ---------------------------------------------------------------------------

test("documentLabel prefers report_name, falls back to company+fiscal info, then to a generic label", () => {
  const ui = createUi(async () => okResponse(answerableBody()));

  // a. company + report_name + fiscal_year가 모두 있는 경우.
  const responseA = {
    facts: [
      {
        fact_id: "fact-a",
        company: "테스트전자",
        fiscal_year: 2025,
        evidence_ids: ["evidence-a"],
      },
    ],
    calculations: [],
    evidence: [
      { evidence_id: "evidence-a", doc_id: "periodic_SYNTHETIC0001" },
    ],
    document_summaries: [
      {
        doc_id: "periodic_SYNTHETIC0001",
        company: "테스트전자",
        report_name: "사업보고서",
      },
    ],
  };

  assert.equal(
    evalInContext(
      ui.context,
      "documentLabel(__evidenceItem, buildEvidenceContext(__response))",
      { __response: responseA, __evidenceItem: responseA.evidence[0] },
    ),
    "테스트전자 2025년 사업보고서",
  );

  // b. document_summaries가 없어 report_name은 못 얻지만, company + fiscal_year +
  //    fiscal_period는 fact로부터 얻는 경우.
  const responseB = {
    facts: [
      {
        fact_id: "fact-b",
        company: "테스트전자",
        fiscal_year: 2025,
        fiscal_period: "half-year",
        evidence_ids: ["evidence-b"],
      },
    ],
    calculations: [],
    evidence: [
      { evidence_id: "evidence-b", doc_id: "periodic_SYNTHETIC0002" },
    ],
    document_summaries: [],
  };

  assert.equal(
    evalInContext(
      ui.context,
      "documentLabel(__evidenceItem, buildEvidenceContext(__response))",
      { __response: responseB, __evidenceItem: responseB.evidence[0] },
    ),
    "테스트전자 2025년 반기보고서",
  );

  // c. 어떤 fact/document_summaries에도 연결되지 않아 metadata가 전혀 없는 경우.
  const responseC = {
    facts: [],
    calculations: [],
    evidence: [
      { evidence_id: "evidence-c", doc_id: "periodic_SYNTHETIC0003" },
    ],
    document_summaries: [],
  };

  assert.equal(
    evalInContext(
      ui.context,
      "documentLabel(__evidenceItem, buildEvidenceContext(__response))",
      { __response: responseC, __evidenceItem: responseC.evidence[0] },
    ),
    "관련 공시 문서",
  );
});

// ---------------------------------------------------------------------------
// 13. Evidence 위치 표기 — row_label/column_label/field_name 중복 제거와
//     "항목" 접미사 규칙.
// ---------------------------------------------------------------------------

test("evidence position labels are de-duplicated and only get the 항목 suffix for row_label/field_name", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        evidence: [
          // row_label과 field_name이 같은 문자열로 중복 — Set으로 한 번만 남고,
          // row_label/field_name 출신이므로 "항목"이 붙는다.
          {
            evidence_id: "e-dup",
            doc_id: "periodic_SYNTHETIC0001",
            row_label: "매출액",
            field_name: "매출액",
          },
          // column_label만 남는 경우 — 원문 그대로, "항목"을 붙이지 않는다.
          {
            evidence_id: "e-column",
            doc_id: "periodic_SYNTHETIC0001",
            column_label: "2025년 3분기",
          },
          // 서로 다른 값이 여러 개 남는 경우 — 그대로 " · "로 나열한다.
          {
            evidence_id: "e-multi",
            doc_id: "periodic_SYNTHETIC0001",
            row_label: "매출액",
            column_label: "2025년 3분기",
          },
        ],
      }),
    ),
  );
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.match(html, /<dd>매출액 항목<\/dd>/);
  assert.match(html, /<dd>2025년 3분기<\/dd>/);
  assert.equal(html.includes("2025년 3분기 항목"), false);
  assert.match(html, /<dd>매출액 · 2025년 3분기<\/dd>/);
});

// ---------------------------------------------------------------------------
// 14. raw provenance(table_id=...) 문자열은 위치/단위 어디에도 노출되지 않는다.
// ---------------------------------------------------------------------------

test("raw internal provenance strings are hidden from the position and unit fields", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        evidence: [
          {
            evidence_id: "e-raw",
            doc_id: "periodic_SYNTHETIC0001",
            row_label: "table_id=table_0127;row_index=5;col_index=1",
            provenance: {
              unit_declaration: "table_id=table_0127;row_index=5;col_index=1",
            },
          },
        ],
      }),
    ),
  );
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.equal(html.includes("table_id="), false);
  assert.equal(html.includes("row_index="), false);
  assert.equal(html.includes("col_index="), false);
});

// ---------------------------------------------------------------------------
// 15. BLOCKED 메시지 줄바꿈 보존과 allowed_report_types pill 버튼.
// ---------------------------------------------------------------------------

test("a BLOCKED response preserves line breaks in the message and renders allowed_report_types as buttons", async () => {
  const ui = createUi(async () => ({
    ok: false,
    status: 422,
    json: async () => ({
      execution_status: "BLOCKED",
      error: {
        stage: "query_plan",
        reason: "ambiguous_report_type",
        message: "보고서 종류를 하나로 정하지 못했습니다.\n아래 중 하나를 선택해 주세요.",
        details: {
          allowed_report_types: ["사업보고서", "반기보고서"],
        },
      },
    }),
  }));
  ui.input.value = "테스트전자 실적 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  // textContent에 "\n"이 그대로 남아 있어야 CSS의 white-space: pre-line이 줄바꿈으로
  // 렌더링할 수 있다 — 여기서 한 줄로 이어붙이면(공백 치환 등) 그 전제가 깨진다.
  assert.equal(
    ui.elements["#state-description"].textContent,
    "보고서 종류를 하나로 정하지 못했습니다.\n아래 중 하나를 선택해 주세요.",
  );

  const suggestions = ui.elements["#state-suggestions"];

  assert.equal(suggestions.hidden, false);

  const buttons = suggestions.querySelectorAll("[data-question-fill]");

  assert.equal(buttons.length, 2);
  assert.equal(buttons[0].dataset.questionFill, "사업보고서");
  assert.equal(buttons[1].dataset.questionFill, "반기보고서");

  // 버튼을 눌러도 기존 질문이 통째로 사라지지 않고, 선택값이 뒤에 덧붙는다. UI는
  // "사업보고서"가 상반기/하반기 중 무엇을 뜻하는지 스스로 판단하지 않는다 — 백엔드가
  // 내려준 문자열을 그대로 붙일 뿐이다.
  buttons[0].click();

  assert.equal(ui.input.value, "테스트전자 실적 알려줘 사업보고서");
  assert.equal(ui.input.focused, true);
});

// ---------------------------------------------------------------------------
// 16. Evidence 위치의 재무제표 주석 번호("(주30)" 등) 제거.
// ---------------------------------------------------------------------------

test("footnote reference numbers are stripped from row_label/field_name, but not from generic parentheses or raw_text", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        evidence: [
          // 괄호로 감싼 형태: "매출액 (주30)" → "매출액".
          {
            evidence_id: "e-paren-spaced",
            doc_id: "periodic_SYNTHETIC0001",
            row_label: "매출액 (주30)",
            raw_text: "매출액 (주30) 1,000,000원",
          },
          // 괄호 안에 공백이 낀 형태: "영업이익(주 12)" → "영업이익".
          {
            evidence_id: "e-paren-inner-space",
            doc_id: "periodic_SYNTHETIC0001",
            row_label: "영업이익(주 12)",
          },
          // 괄호 없이 단독으로 붙은 형태: "주30"만 남으면 후보 자체를 지운다.
          {
            evidence_id: "e-bare-only",
            doc_id: "periodic_SYNTHETIC0001",
            field_name: "주30",
          },
          // 실제 corpus에서 확인된, 주석이 아닌 일반 괄호: "수익(매출액)"은 그대로.
          {
            evidence_id: "e-generic-parens",
            doc_id: "periodic_SYNTHETIC0001",
            row_label: "수익(매출액)",
          },
        ],
      }),
    ),
  );
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.match(html, /<dd>매출액 항목<\/dd>/);
  assert.match(html, /<dd>영업이익 항목<\/dd>/);
  assert.match(html, /<dd>수익\(매출액\) 항목<\/dd>/);

  // "위치" dd 안에는 주석 번호가 남아 있지 않아야 한다. raw_text에는 "주30"이
  // 의도적으로 그대로 남으므로(아래에서 검증), html 전체가 아니라 <dd> 값만 본다.
  const positionValues = [...html.matchAll(/<dt>근거 항목<\/dt>\s*<dd>([\s\S]*?)<\/dd>/g)].map(
    (match) => match[1],
  );

  assert.equal(
    positionValues.some((value) => /주\s*\d/.test(value)),
    false,
  );

  // raw_text(원문 발췌)는 절대 건드리지 않는다.
  assert.match(html, /매출액 \(주30\) 1,000,000원/);

  // "주30" 하나만 있던 세 번째 evidence(e-bare-only, 배열 순서상 3번째 article)는
  // 위치 후보가 통째로 사라져야 하므로 "위치" dt 자체가 없어야 한다 — 안 그러면
  // 빈 <dd></dd>가 남는다. evidence_id는 화면에 나오지 않으므로 배열 순서로 찾는다.
  const articles = html.match(/<article class="evidence-item">[\s\S]*?<\/article>/g);

  assert.equal(articles.length, 4);
  assert.equal(articles[2].includes("<dt>근거 항목</dt>"), false);
});

// ---------------------------------------------------------------------------
// 17. 문서명 — document_summaries의 report_name이 있으면 실제 보고서 종류를 쓴다.
// ---------------------------------------------------------------------------

test("a business-report (사업보고서) evidence linked via document_summaries.report_name renders the real report type, not a generic 공시 label", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        facts: [
          {
            fact_id: "fact-samsung",
            company: "삼성전자",
            metric: "revenue",
            fiscal_year: 2025,
            accounting_scope: "consolidated",
            evidence_ids: ["evidence-samsung"],
          },
        ],
        evidence: [
          {
            evidence_id: "evidence-samsung",
            doc_id: "periodic_SYNTHETIC0099",
            section_path: ["III. 재무에 관한 사항"],
            row_label: "매출액",
          },
        ],
        document_summaries: [
          {
            doc_id: "periodic_SYNTHETIC0099",
            company: "삼성전자",
            report_name: "사업보고서",
          },
        ],
      }),
    ),
  );
  ui.input.value = "삼성전자 2025년 매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.match(html, /삼성전자 2025년 사업보고서/);
  assert.equal(html.includes("periodic_SYNTHETIC0099"), false);
});

// ---------------------------------------------------------------------------
// 18. 문서명 — report_name도 fiscal_period도 없으면(대부분의 단일 수치 조회가
//     실제로 이 경우다) 사업보고서라고 추정하지 않고 정직한 폴백을 쓴다.
// ---------------------------------------------------------------------------

test("without report_name or fiscal_period, the document label falls back honestly instead of guessing 사업보고서", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        facts: [
          {
            fact_id: "fact-samsung-2",
            company: "삼성전자",
            metric: "revenue",
            fiscal_year: 2025,
            // 실제 프로덕션 Pattern A(단일 Fact 조회)는 fiscal_period를 채우지
            // 않는다 — periodic/adapter.py의 adapt_financial_fact가 항상 None을
            // 넣는다. document_summaries도 이 경로에서는 만들어지지 않는다.
            fiscal_period: null,
            accounting_scope: "consolidated",
            evidence_ids: ["evidence-samsung-2"],
          },
        ],
        evidence: [
          {
            evidence_id: "evidence-samsung-2",
            doc_id: "periodic_SYNTHETIC0100",
            row_label: "매출액",
          },
        ],
        document_summaries: [],
      }),
    ),
  );
  ui.input.value = "삼성전자 2025년 매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.match(html, /삼성전자 2025년 공시/);
  assert.equal(html.includes("사업보고서"), false);
  assert.equal(html.includes("periodic_SYNTHETIC0100"), false);
});

// ---------------------------------------------------------------------------
// 19. Evidence 위치 — 사람이 읽는 row_label/column_label이 있으면 내부
//     snake_case field_name은 함께 보이지 않는다.
// ---------------------------------------------------------------------------

test("an internal snake_case field_name is hidden from the position whenever a human-readable label exists", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        evidence: [
          // row_label이 있으면 field_name("investment_amount")은 완전히 빠진다.
          {
            evidence_id: "e-row-and-field",
            doc_id: "exchange_SYNTHETIC0001",
            row_label: "투자금액(원)",
            field_name: "investment_amount",
          },
          // row_label/column_label이 둘 다 없고 field_name만 있을 때는,
          // FIELD_LABELS에 명시적 매핑이 있으면 그 한글 라벨을 쓴다.
          {
            evidence_id: "e-field-only-mapped",
            doc_id: "exchange_SYNTHETIC0001",
            field_name: "investment_amount",
          },
          // field_name만 있고 매핑이 없으면 임의로 번역하지 않고 위치 자체를
          // 표시하지 않는다.
          {
            evidence_id: "e-field-only-unmapped",
            doc_id: "exchange_SYNTHETIC0001",
            field_name: "some_unmapped_internal_key",
          },
        ],
      }),
    ),
  );
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;
  const articles = html.match(/<article class="evidence-item">[\s\S]*?<\/article>/g);

  assert.equal(articles.length, 3);

  assert.match(articles[0], /<dd>투자금액\(원\) 항목<\/dd>/);
  assert.equal(articles[0].includes("investment_amount"), false);

  assert.match(articles[1], /<dd>투자금액 항목<\/dd>/);
  assert.equal(articles[1].includes("investment_amount"), false);

  assert.equal(articles[2].includes("<dt>근거 항목</dt>"), false);
  assert.equal(articles[2].includes("some_unmapped_internal_key"), false);
});

// ---------------------------------------------------------------------------
// 20. 답변 문장의 원화 정수 → 조/억/만 보조 표기 삽입.
// ---------------------------------------------------------------------------

test("insertKoreanWonBreakdown converts plain KRW integers in the answer sentence, leaving everything else untouched", () => {
  const ui = createUi(async () => okResponse(answerableBody()));

  const run = (text) =>
    evalInContext(ui.context, "insertKoreanWonBreakdown(__text)", { __text: text });

  // 명세에 있는 두 예시를 그대로 고정한다.
  assert.equal(run("758900000000원"), "7,589억 원");
  assert.equal(run("28533520000원"), "285억 3,352만 원");

  // 실제 답변 문장 모양 그대로("LG이노텍의 시설투자 합계는 758,900,000,000원입니다.")
  assert.equal(
    run("LG이노텍의 시설투자 합계는 758,900,000,000원입니다."),
    "LG이노텍의 시설투자 합계는 7,589억 원입니다.",
  );

  // 음수(증감액 등)도 부호를 보존한다.
  assert.equal(run("-758900000000원"), "-7,589억 원");

  // 1만원 미만은 변환할 더 굵은 단위가 없으므로 원본 그대로 둔다.
  assert.equal(run("100원"), "100원");

  // 연도·비율·주식 수는 접미사가 달라 애초에 매칭되지 않는다.
  assert.equal(run("2025년 매출액은 12.34%입니다."), "2025년 매출액은 12.34%입니다.");
  assert.equal(run("보유주식 수는 1,237,964,708주입니다."), "보유주식 수는 1,237,964,708주입니다.");

  // 이미 조/억/만 단위가 붙은 표현은 다시 변환하지 않는다.
  assert.equal(run("매출액은 17조 5,806억원입니다."), "매출액은 17조 5,806억원입니다.");
  assert.equal(run("매출액은 1,000억원입니다."), "매출액은 1,000억원입니다.");
});

// ---------------------------------------------------------------------------
// 21. insertKoreanWonBreakdown — 천원/백만원 단위 확장, BigInt 정밀도, XSS 방어.
// ---------------------------------------------------------------------------

test("insertKoreanWonBreakdown converts 백만원/천원-denominated amounts, exactly and without precision loss", () => {
  const ui = createUi(async () => okResponse(answerableBody()));

  const run = (text) =>
    evalInContext(ui.context, "insertKoreanWonBreakdown(__text)", { __text: text });

  // 쉼표가 있는 백만원.
  assert.equal(run("333,605,938백만원"), "333조 6,059억 3,800만 원");

  // 쉼표가 없는 백만원.
  assert.equal(run("758900백만원"), "7,589억 원");

  // "백만 원" 띄어쓰기(숫자-단위 사이, 단위 내부 모두).
  assert.equal(run("333,605,938 백만원"), "333조 6,059억 3,800만 원");
  assert.equal(run("333,605,938 백만 원"), "333조 6,059억 3,800만 원");

  // 천원 및 "천 원" 띄어쓰기.
  assert.equal(run("28,533,520천원"), "285억 3,352만 원");
  assert.equal(run("28,533,520 천 원"), "285억 3,352만 원");
  assert.equal(run("28,533,520 천원"), "285억 3,352만 원");

  // 원 단위 기존 동작 회귀 없음.
  assert.equal(run("758900000000원"), "7,589억 원");

  // 조/억/만 혼합 — acceptance 문장 그대로.
  assert.equal(
    run("삼성전자의 2025년 연결 기준 매출액은 333,605,938백만원입니다."),
    "삼성전자의 2025년 연결 기준 매출액은 333조 6,059억 3,800만 원입니다.",
  );

  // 음수 부호 보존 — 백만원/천원 단위에도 적용된다.
  assert.equal(run("-758900백만원"), "-7,589억 원");
  assert.equal(run("증감액은 -28,533,520천원입니다."), "증감액은 -285억 3,352만 원입니다.");

  // 1만원 미만은 백만원/천원 환산 후에도 변환할 굵은 단위가 없으면 원문 그대로 둔다.
  assert.equal(run("5천원"), "5천원");
  assert.equal(run("9원"), "9원");

  // 이미 한국식 단위로 변환된 표현은 다시 변환하지 않는다(조/억/만 혼합 포함).
  assert.equal(run("매출액은 333조 6,059억 3,800만 원입니다."), "매출액은 333조 6,059억 3,800만 원입니다.");
  assert.equal(run("매출액은 285억 3,352만 원입니다."), "매출액은 285억 3,352만 원입니다.");

  // 일반 숫자·연도·비율·주식 수·접수번호는 원화 단위가 없으므로 변환되지 않는다.
  assert.equal(run("접수번호 20250101000123입니다."), "접수번호 20250101000123입니다.");
  assert.equal(run("2025년 매출액은 12.34%입니다."), "2025년 매출액은 12.34%입니다.");
  assert.equal(run("보유주식 수는 1,237,964,708주입니다."), "보유주식 수는 1,237,964,708주입니다.");
  assert.equal(run("총 333,605,938개입니다."), "총 333,605,938개입니다.");

  // 큰 정수에서 Number 곱셈이라면 잃을 수 있는 정밀도를 BigInt로 유지한다. 기대값도
  // BigInt 연산으로 직접 계산해(구현과 같은 조/억/만/원 분해) 부동소수점 경로를
  // 거치지 않는다.
  const hugeMillions = "999,999,999,999백만원";
  const expectedWon = 999999999999n * 1000000n; // 999,999,999,999,000,000원
  const expectedTrillion = expectedWon / 1_000_000_000_000n;
  const afterTrillion = expectedWon % 1_000_000_000_000n;
  const expectedEok = afterTrillion / 100_000_000n;
  const afterEok = afterTrillion % 100_000_000n;
  const expectedMan = afterEok / 10_000n;
  assert.equal(
    run(hugeMillions),
    `${expectedTrillion.toLocaleString("ko-KR")}조 ${expectedEok.toLocaleString("ko-KR")}억 ${expectedMan.toLocaleString("ko-KR")}만 원`,
  );
});

test("the answer summary is rendered via textContent, so HTML in the answer is shown as literal text (no XSS)", async () => {
  const maliciousAnswer =
    "삼성전자의 매출액은 <script>alert(1)</script> 758,900,000,000원입니다.";

  const ui = createUi(async () =>
    okResponse(answerableBody({ answer: maliciousAnswer })),
  );
  ui.input.value = "삼성전자 매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  // textContent에는 태그가 문자 그대로 남아 있어야 한다(innerHTML로 바뀌어
  // 파싱되지 않는다). 원화 단위 변환은 그대로 동작해야 한다.
  assert.equal(
    ui.elements["#answer-summary"].textContent,
    "삼성전자의 매출액은 <script>alert(1)</script> 7,589억 원입니다.",
  );

  // app.js 소스 자체가 답변 렌더링에 innerHTML을 쓰지 않는지도 함께 확인한다.
  assert.equal(/fields\.answer\.innerHTML/.test(appSource), false);
});

// ---------------------------------------------------------------------------
// 22. source_documents(신규 additive 필드) — doc_id join으로 문서 라벨을
//     추측 없이 표시한다. document_subtype/base_month는 SourceDocument가 내려준
//     값만 쓰고, report_name이 있으면 항상 그것을 우선한다.
// ---------------------------------------------------------------------------

test("sourceDocumentLabel builds report labels from document_subtype + base_month, and report_name always wins", () => {
  const ui = createUi(async () => okResponse(answerableBody()));

  const label = (sourceDoc) =>
    evalInContext(ui.context, "sourceDocumentLabel(__doc)", { __doc: sourceDoc });

  // annual + base_month=12.
  assert.equal(
    label({
      doc_id: "periodic_SD0001",
      company: "삼성전자",
      fiscal_year: 2025,
      document_subtype: "annual",
      base_month: 12,
    }),
    "삼성전자 2025년 사업보고서",
  );

  // quarter + base_month=3 → 1분기.
  assert.equal(
    label({
      doc_id: "periodic_SD0002",
      company: "삼성전자",
      fiscal_year: 2025,
      document_subtype: "quarter",
      base_month: 3,
    }),
    "삼성전자 2025년 1분기보고서",
  );

  // half + base_month=6 → 반기(분기 번호를 매기지 않는다).
  assert.equal(
    label({
      doc_id: "periodic_SD0003",
      company: "삼성전자",
      fiscal_year: 2025,
      document_subtype: "half",
      base_month: 6,
    }),
    "삼성전자 2025년 반기보고서",
  );

  // quarter + base_month=9 → 3분기.
  assert.equal(
    label({
      doc_id: "periodic_SD0004",
      company: "삼성전자",
      fiscal_year: 2025,
      document_subtype: "quarter",
      base_month: 9,
    }),
    "삼성전자 2025년 3분기보고서",
  );

  // report_name에 "(2025.12)"처럼 결산연도.월이 이미 포함되어 있어도, 연도를
  // 중복 표시하지 않고 표준 "{fiscal_year}년 {report_name}" 형식 하나로 통일한다.
  assert.equal(
    label({
      doc_id: "periodic_SD0005",
      company: "삼성전자",
      fiscal_year: 2025,
      report_name: "사업보고서 (2025.12)",
      // document_subtype이 함께 와도 report_name이 항상 우선한다(요구사항 5).
      document_subtype: "quarter",
      base_month: 3,
    }),
    "삼성전자 2025년 사업보고서",
  );
});

test("evidence linked to source_documents shows the exact metadata label, not a guess from document_subtype/base_month alone", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        source_documents: [
          {
            doc_id: "periodic_SD0010",
            company: "삼성전자",
            fiscal_year: 2025,
            document_subtype: "annual",
            base_month: 12,
            filing_date: "2026-03-15",
            receipt_no: "20260315000123",
          },
        ],
        evidence: [
          {
            evidence_id: "e-annual",
            doc_id: "periodic_SD0010",
            section_path: ["III. 재무에 관한 사항"],
            row_label: "매출액",
          },
        ],
      }),
    ),
  );
  ui.input.value = "삼성전자 2025년 매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.match(html, /삼성전자 2025년 사업보고서/);
  assert.equal(html.includes("periodic_SD0010"), false);
  // 기존 section_path 표시는 그대로 유지된다.
  assert.match(html, /III\. 재무에 관한 사항/);
});

test("the same source document referenced by multiple evidence items renders the identical document label for both", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        source_documents: [
          {
            doc_id: "periodic_SD0020",
            company: "삼성전자",
            fiscal_year: 2025,
            document_subtype: "quarter",
            base_month: 9,
          },
        ],
        evidence: [
          {
            evidence_id: "e-first",
            doc_id: "periodic_SD0020",
            row_label: "매출액",
          },
          {
            evidence_id: "e-second",
            doc_id: "periodic_SD0020",
            row_label: "영업이익",
          },
        ],
      }),
    ),
  );
  ui.input.value = "삼성전자 2025년 3분기 실적 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;
  const occurrences = html.match(/삼성전자 2025년 3분기보고서/g) || [];

  assert.equal(occurrences.length, 2);
  assert.equal(html.includes("periodic_SD0020"), false);
});

test("without source_documents in the response, the existing fact/evidence context fallback still renders a document label", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        facts: [
          {
            fact_id: "fact-fallback",
            company: "삼성전자",
            metric: "revenue",
            fiscal_year: 2025,
            fiscal_period: "half-year",
            evidence_ids: ["e-fallback"],
          },
        ],
        evidence: [
          {
            evidence_id: "e-fallback",
            doc_id: "periodic_SD0030",
            row_label: "매출액",
          },
        ],
        // source_documents 필드 자체가 없다 — 기존 context fallback 경로로 가야 한다.
      }),
    ),
  );
  ui.input.value = "삼성전자 2025년 반기 매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.match(html, /삼성전자 2025년 반기보고서/);
  assert.equal(html.includes("periodic_SD0030"), false);
});

test("when an evidence doc_id has no matching entry in source_documents, the join fails silently and no periodic_* doc_id leaks to the screen", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        source_documents: [
          {
            doc_id: "periodic_SD0040",
            company: "삼성전자",
            fiscal_year: 2025,
            document_subtype: "annual",
          },
        ],
        evidence: [
          {
            evidence_id: "e-unmatched",
            // source_documents에는 없는 doc_id — join 실패 케이스.
            doc_id: "periodic_SD_UNMATCHED",
            row_label: "매출액",
          },
        ],
      }),
    ),
  );
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.equal(html.includes("periodic_SD_UNMATCHED"), false);
  assert.equal(html.includes("periodic_SD0040"), false);
  assert.match(html, /관련 공시 문서/);
});

// ---------------------------------------------------------------------------
// 23. subtypeReportLabel — exact allowlist만 허용한다. quarter의 분기 번호를
//     base_month로부터 계산하지 않으며, allowlist에 없는 조합/숫자가 아닌
//     base_month는 전부 null이다(호출부가 report_name/기존 fallback으로 넘어간다).
// ---------------------------------------------------------------------------

test("subtypeReportLabel only recognizes the exact allowlisted subtype+base_month combinations, never computing a quarter number", () => {
  const ui = createUi(async () => okResponse(answerableBody()));

  const resolve = (documentSubtype, baseMonth) =>
    evalInContext(ui.context, "subtypeReportLabel(__subtype, __month)", {
      __subtype: documentSubtype,
      __month: baseMonth,
    });

  // 허용된 조합.
  assert.equal(resolve("annual", 12), "사업보고서");
  assert.equal(resolve("half", 6), "반기보고서");
  assert.equal(resolve("quarter", 3), "1분기보고서");
  assert.equal(resolve("quarter", 9), "3분기보고서");

  // 허용되지 않은 조합 — 계산식이 있었다면 "2분기보고서"/"4분기보고서"를 만들어
  // 냈겠지만, allowlist에 없으므로 null이어야 한다.
  assert.equal(resolve("quarter", 6), null);
  assert.equal(resolve("quarter", 12), null);
  assert.equal(resolve("half", 3), null);
  assert.equal(resolve("annual", 9), null);

  // base_month 누락(null/undefined) 및 숫자가 아닌 값.
  assert.equal(resolve("quarter", null), null);
  assert.equal(resolve("quarter", undefined), null);
  assert.equal(resolve("quarter", "3"), null);
  assert.equal(resolve("quarter", "삼월"), null);
});

test("an evidence item whose source document has a non-allowlisted subtype+base_month combination falls back honestly instead of a guessed label", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        source_documents: [
          {
            doc_id: "periodic_SD0050",
            company: "삼성전자",
            fiscal_year: 2025,
            // quarter + base_month=6은 실제로 존재하지 않는 조합 — 계산식이었다면
            // "2분기보고서"를 만들어 냈을 것이다.
            document_subtype: "quarter",
            base_month: 6,
          },
        ],
        evidence: [
          {
            evidence_id: "e-invalid-combo",
            doc_id: "periodic_SD0050",
            row_label: "매출액",
          },
        ],
      }),
    ),
  );
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.equal(html.includes("2분기보고서"), false);
  assert.equal(html.includes("periodic_SD0050"), false);
  assert.match(html, /관련 공시 문서/);
});

// ---------------------------------------------------------------------------
// 24. report_name의 날짜 suffix("(2025.12)")는 fiscal_year가 있고 그 연도가
//     suffix의 연도와 정확히 일치할 때만 표준 형식으로 재구성한다. fiscal_year가
//     없으면 backend가 준 report_name을 그대로 보존하고, 두 연도가 어긋나면
//     충돌을 감춘 문서명을 합성하는 대신 fail-close(null)한다
//     (UI가 backend 정보를 임의로 버리거나 덮어쓰지 않는다).
// ---------------------------------------------------------------------------

test("the report_name date suffix is only reconstructed away when fiscal_year is present; without it, the suffix is preserved verbatim", () => {
  const ui = createUi(async () => okResponse(answerableBody()));

  const label = (sourceDoc) =>
    evalInContext(ui.context, "sourceDocumentLabel(__doc)", { __doc: sourceDoc });

  // fiscal_year=2025 → "(2025.12)"를 떼고 표준 "2025년 사업보고서" 형식으로.
  assert.equal(
    label({
      doc_id: "periodic_SD0060",
      company: "삼성전자",
      fiscal_year: 2025,
      report_name: "사업보고서 (2025.12)",
    }),
    "삼성전자 2025년 사업보고서",
  );

  // fiscal_year가 null이면 대체할 연도 정보가 없으므로 report_name을 원문 그대로
  // 보존한다 — "(2025.12)"를 지우지 않는다.
  assert.equal(
    label({
      doc_id: "periodic_SD0061",
      company: "삼성전자",
      fiscal_year: null,
      report_name: "사업보고서 (2025.12)",
    }),
    "삼성전자 사업보고서 (2025.12)",
  );

  // fiscal_year가 아예 없는(undefined) 경우도 동일하게 보존한다.
  assert.equal(
    label({
      doc_id: "periodic_SD0062",
      company: "삼성전자",
      report_name: "사업보고서 (2025.12)",
    }),
    "삼성전자 사업보고서 (2025.12)",
  );
});

test("an evidence item whose source document has report_name with a date suffix but no fiscal_year keeps the suffix on screen", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        source_documents: [
          {
            doc_id: "periodic_SD0063",
            company: "삼성전자",
            fiscal_year: null,
            report_name: "사업보고서 (2025.12)",
          },
        ],
        evidence: [
          {
            evidence_id: "e-no-fiscal-year",
            doc_id: "periodic_SD0063",
            row_label: "매출액",
          },
        ],
      }),
    ),
  );
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.match(html, /삼성전자 사업보고서 \(2025\.12\)/);
  assert.equal(html.includes("periodic_SD0063"), false);
});

test("a report_name date suffix whose year conflicts with fiscal_year fails closed instead of synthesizing a document name", () => {
  const ui = createUi(async () => okResponse(answerableBody()));

  const label = (sourceDoc) =>
    evalInContext(ui.context, "sourceDocumentLabel(__doc)", { __doc: sourceDoc });

  // 일치: fiscal_year=2025 + "(2025.12)" → 기존 동작 그대로 표준 형식으로 재구성.
  assert.equal(
    label({
      doc_id: "periodic_SD0070",
      company: "삼성전자",
      fiscal_year: 2025,
      report_name: "사업보고서 (2025.12)",
    }),
    "삼성전자 2025년 사업보고서",
  );

  // 불일치: fiscal_year=2025인데 report_name suffix는 2024 — backend metadata가
  // 충돌한 상태다. suffix의 2024를 조용히 버리고 "2025년 사업보고서"를 만들거나,
  // 연도만 지운 "사업보고서"를 만들지 않고 라벨 생성 자체를 포기한다.
  assert.equal(
    label({
      doc_id: "periodic_SD0071",
      company: "삼성전자",
      fiscal_year: 2025,
      report_name: "사업보고서 (2024.12)",
    }),
    null,
  );

  // subtype 매핑이 함께 와도 마찬가지다 — 충돌한 report_name을 우회해 subtype으로
  // 그럴듯한 라벨을 만들어내지 않는다(SourceDocument 기반 생성 전체를 fail-close).
  assert.equal(
    label({
      doc_id: "periodic_SD0072",
      company: "삼성전자",
      fiscal_year: 2025,
      report_name: "사업보고서 (2024.12)",
      document_subtype: "annual",
      base_month: 12,
    }),
    null,
  );

  // suffix가 없는 report_name은 비교 대상 자체가 없으므로 기존 동작을 유지한다.
  assert.equal(
    label({
      doc_id: "periodic_SD0073",
      company: "삼성전자",
      fiscal_year: 2025,
      report_name: "사업보고서",
    }),
    "삼성전자 2025년 사업보고서",
  );

  // 연도.월 형식이 아닌 괄호는 지원 범위 밖이라 비교하지 않고 원문을 보존한다.
  assert.equal(
    label({
      doc_id: "periodic_SD0074",
      company: "삼성전자",
      fiscal_year: 2025,
      report_name: "사업보고서 (수정)",
    }),
    "삼성전자 2025년 사업보고서 (수정)",
  );
});

test("evidence whose source document has a conflicting fiscal_year/report_name year falls back to the legacy label without leaking the raw doc_id", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        source_documents: [
          {
            doc_id: "periodic_SD0075",
            company: "삼성전자",
            fiscal_year: 2025,
            report_name: "사업보고서 (2024.12)",
            filing_date: "2026-03-15",
          },
        ],
        evidence: [
          {
            evidence_id: "e-year-conflict",
            doc_id: "periodic_SD0075",
            section_path: ["III. 재무에 관한 사항"],
            row_label: "매출액",
          },
        ],
      }),
    ),
  );
  ui.input.value = "삼성전자 2025년 매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  // 충돌한 metadata로 합성한 문서명이 화면에 나오지 않는다.
  assert.equal(html.includes("삼성전자 2025년 사업보고서"), false);
  assert.equal(html.includes("2025년 사업보고서"), false);
  // 질문 문자열("삼성전자 2025년 매출")로부터 보고서명을 추측하지도 않는다.
  assert.equal(html.includes("사업보고서"), false);
  // 기존 legacy documentLabel fallback이 쓰인다.
  assert.match(html, /관련 공시 문서/);
  // raw periodic_* doc_id는 여전히 노출되지 않는다.
  assert.equal(html.includes("periodic_SD0075"), false);
  // Evidence panel과 section_path는 그대로 유지된다.
  assert.equal(ui.elements["#evidence-panel"].hidden, false);
  assert.match(html, /III\. 재무에 관한 사항/);
});

// ---------------------------------------------------------------------------
// 25. formatFilingDate — 백엔드 ISO "YYYY-MM-DD"를 화면 표기 "YYYY.MM.DD"로
//     바꾼다. 정확히 그 형식이고 실재하는 달력 날짜일 때만 값을 돌려주고, 그 외는
//     null이다(오늘 날짜나 질문 연도로 추측하지 않는다 — 호출부가 행을 숨긴다).
// ---------------------------------------------------------------------------

test("formatFilingDate converts a valid ISO date and rejects anything else", () => {
  const ui = createUi(async () => okResponse(answerableBody()));

  const run = (value) =>
    evalInContext(ui.context, "formatFilingDate(__value)", { __value: value });

  assert.equal(run("2026-03-10"), "2026.03.10");

  // null/undefined — 값 자체가 없다.
  assert.equal(run(null), null);
  assert.equal(run(undefined), null);

  // 형식이 다른 경우(구분자, 자릿수, 순서 등).
  assert.equal(run("2026/03/10"), null);
  assert.equal(run("2026-3-10"), null);
  assert.equal(run("10-03-2026"), null);
  assert.equal(run("2026-03-10T00:00:00Z"), null);
  assert.equal(run(""), null);
  assert.equal(run("not-a-date"), null);

  // 형식은 맞지만 실재하지 않는 달력 날짜(윤년 아닌 해의 2/29, 2월 30일, 13월).
  assert.equal(run("2024-02-30"), null);
  assert.equal(run("2025-02-29"), null);
  assert.equal(run("2026-13-01"), null);

  // 실재하는 윤년 2/29는 통과한다.
  assert.equal(run("2024-02-29"), "2024.02.29");
});

// ---------------------------------------------------------------------------
// 26. SourceDocument join 성공 — 문서명/section_path/근거 항목/제출일이 모두
//     명세대로 표시되고, raw periodic_* doc_id와 내부 provenance는 노출되지 않는다.
// ---------------------------------------------------------------------------

test("a full SourceDocument join renders document name, section_path, evidence item label, and filing date, without leaking raw doc_id or provenance", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        source_documents: [
          {
            doc_id: "periodic_SD0070",
            company: "삼성전자",
            report_name: "사업보고서 (2025.12)",
            fiscal_year: 2025,
            filing_date: "2026-03-10",
          },
        ],
        evidence: [
          {
            evidence_id: "e-full-join",
            doc_id: "periodic_SD0070",
            section_path: ["III. 재무에 관한 사항", "2. 연결재무제표"],
            row_label: "매출액 (주30)",
            raw_text: "매출액 758,900,000,000원",
          },
        ],
      }),
    ),
  );
  ui.input.value = "삼성전자 2025년 매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  // 카드 제목: report_name 우선, 날짜 suffix는 fiscal_year로 대체된다.
  assert.match(html, /삼성전자 2025년 사업보고서/);

  // section_path — 배열 순서를 그대로, hierarchy가 읽히게(" › " 구분).
  assert.match(html, /III\. 재무에 관한 사항 › 2\. 연결재무제표/);

  // raw_text 원문은 그대로.
  assert.match(html, /매출액 758,900,000,000원/);

  // 근거 항목 — 주석 번호("(주30)")는 떼고 사람이 읽게 표시한다.
  assert.match(html, /<dt>근거 항목<\/dt>\s*<dd>매출액 항목<\/dd>/);

  // 제출일 — ISO를 화면 표기로 변환.
  assert.match(html, /<dt>제출일<\/dt>\s*<dd>2026\.03\.10<\/dd>/);

  // 내부 provenance/raw doc_id는 어디에도 없어야 한다.
  assert.equal(html.includes("periodic_SD0070"), false);
  assert.equal(html.includes("table_id="), false);
  assert.equal(html.includes("row_index="), false);
  assert.equal(html.includes("col_index="), false);
});

// ---------------------------------------------------------------------------
// 27. 제출일 — 값이 없거나 형식이 유효하지 않으면 그 행 자체가 렌더링되지 않는다
//     (join은 성공했지만 filing_date 자체가 문제인 경우).
// ---------------------------------------------------------------------------

for (const [label, filingDate] of [
  ["missing (undefined)", undefined],
  ["null", null],
  ["invalid format", "2026/03/10"],
  ["invalid calendar date", "2024-02-30"],
]) {
  test(`filing date row is hidden when filing_date is ${label}`, async () => {
    const ui = createUi(async () =>
      okResponse(
        answerableBody({
          source_documents: [
            {
              doc_id: "periodic_SD0080",
              company: "삼성전자",
              fiscal_year: 2025,
              document_subtype: "annual",
              base_month: 12,
              ...(filingDate === undefined ? {} : { filing_date: filingDate }),
            },
          ],
          evidence: [
            {
              evidence_id: "e-filing-date-hidden",
              doc_id: "periodic_SD0080",
              row_label: "매출액",
            },
          ],
        }),
      ),
    );
    ui.input.value = "삼성전자 2025년 매출 알려줘";

    await ui.form.listeners.submit({ preventDefault() {} });

    const html = ui.elements["#evidence-list"].innerHTML;

    assert.equal(html.includes("<dt>제출일</dt>"), false);
    // join 자체는 성공했으므로 문서명은 여전히 정확히 표시된다.
    assert.match(html, /삼성전자 2025년 사업보고서/);
  });
}

// ---------------------------------------------------------------------------
// 28. 제출일 — source_documents join이 실패하면(doc_id 불일치, 배열 없음/빈
//     배열) filing_date가 있어도 절대 표시하지 않는다 — join되지 않은 다른
//     문서의 제출일을 잘못 붙이면 안 된다.
// ---------------------------------------------------------------------------

test("filing date is never shown when the evidence doc_id does not join to source_documents", async () => {
  const ui = createUi(async () =>
    okResponse(
      answerableBody({
        source_documents: [
          {
            doc_id: "periodic_SD0090",
            company: "삼성전자",
            fiscal_year: 2025,
            document_subtype: "annual",
            base_month: 12,
            filing_date: "2026-03-10",
          },
        ],
        evidence: [
          {
            evidence_id: "e-no-join",
            // source_documents에 없는 doc_id.
            doc_id: "periodic_SD_UNMATCHED_2",
            row_label: "매출액",
          },
        ],
      }),
    ),
  );
  ui.input.value = "매출 알려줘";

  await ui.form.listeners.submit({ preventDefault() {} });

  const html = ui.elements["#evidence-list"].innerHTML;

  assert.equal(html.includes("<dt>제출일</dt>"), false);
  assert.equal(html.includes("2026.03.10"), false);
  assert.equal(html.includes("periodic_SD0090"), false);
  assert.equal(html.includes("periodic_SD_UNMATCHED_2"), false);
});

test("filing date is hidden when source_documents is missing or empty entirely", async () => {
  for (const overrides of [{}, { source_documents: [] }]) {
    const ui = createUi(async () =>
      okResponse(
        answerableBody({
          ...overrides,
          evidence: [
            {
              evidence_id: "e-no-source-documents",
              doc_id: "periodic_SD0091",
              row_label: "매출액",
            },
          ],
        }),
      ),
    );
    ui.input.value = "매출 알려줘";

    await ui.form.listeners.submit({ preventDefault() {} });

    const html = ui.elements["#evidence-list"].innerHTML;

    assert.equal(html.includes("<dt>제출일</dt>"), false);
    assert.equal(html.includes("periodic_SD0091"), false);
    // join 실패 시의 기존 안전한 fallback 라벨은 유지된다.
    assert.match(html, /관련 공시 문서/);
  }
});
