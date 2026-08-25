const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const appSource = fs.readFileSync(
  path.resolve(__dirname, "../../src/disclosure_agent/api/ui/app.js"),
  "utf8",
);

class FakeClassList {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  contains(value) { return this.values.has(value); }
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

  addEventListener(name, handler) { this.listeners[name] = handler; }
  focus() {}
  querySelector() { return this.submitButton; }
  scrollIntoView() {}
  setAttribute(name, value) { this.attributes[name] = value; }
}

const createUi = (fetchResult) => {
  const selectors = [
    "#question-form", "#question", ".result-card", "#form-message", "#state-card",
    ".state-icon", "#state-title", "#state-description", "#sample-button", "#company",
    "#report-meta", "#metric-label", "#metric-value", "#metric-change", "#answer-summary",
    "#status-badge", "#data-mode", "#evidence-count", "#evidence-list", "#results",
  ];
  const elements = Object.fromEntries(selectors.map((selector) => [selector, new FakeElement()]));
  elements["#question-form"].submitButton = new FakeElement();
  elements[".result-card"].hidden = true;
  elements[".result-card"].dataset.mode = "none";
  elements["#sample-button"].hidden = true;
  elements["#results"].hidden = true;

  const document = {
    querySelector: (selector) => elements[selector],
    querySelectorAll: () => [],
  };
  const context = {
    document,
    fetch: async () => fetchResult,
  };
  vm.runInNewContext(appSource, context);

  return { elements, form: elements["#question-form"], input: elements["#question"] };
};

test("API 200 renders a real result", async () => {
  const ui = createUi({
    ok: true,
    status: 200,
    json: async () => ({
      answer: "검증된 API 응답",
      status: "answerable",
      facts: [{ company: "테스트회사", metric: "revenue", raw_value: "100", raw_unit: "원" }],
      evidence: [],
    }),
  });
  ui.input.value = "매출액 질문";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(ui.elements[".result-card"].hidden, false);
  assert.equal(ui.elements["#results"].hidden, false);
  assert.equal(ui.elements[".result-card"].dataset.mode, "real");
  assert.equal(ui.elements["#data-mode"].textContent, "API 응답");
  assert.equal(ui.elements["#company"].textContent, "테스트회사");
});

test("API 503 shows an error without automatically rendering sample data", async () => {
  const ui = createUi({ ok: false, status: 503 });
  ui.input.value = "연결 실패 질문";

  await ui.form.listeners.submit({ preventDefault() {} });

  assert.equal(ui.elements[".result-card"].hidden, true);
  assert.equal(ui.elements["#results"].hidden, false);
  assert.equal(ui.elements[".result-card"].dataset.mode, "none");
  assert.equal(ui.elements["#state-title"].textContent, "현재 공시 Agent와 연결할 수 없습니다.");
  assert.equal(ui.elements["#sample-button"].hidden, false);
  assert.equal(ui.elements["#data-mode"].hidden, true);
});

test("sample data appears only after the user clicks its button", async () => {
  const ui = createUi({ ok: false, status: 503 });
  ui.input.value = "샘플 확인 질문";
  await ui.form.listeners.submit({ preventDefault() {} });

  await ui.elements["#sample-button"].listeners.click();

  assert.equal(ui.elements[".result-card"].hidden, false);
  assert.equal(ui.elements[".result-card"].dataset.mode, "sample");
  assert.equal(ui.elements["#data-mode"].textContent, "SAMPLE · UI 확인용 데이터");
  assert.equal(ui.elements["#status-badge"].hidden, true);
  assert.equal(ui.elements["#form-message"].textContent, "");
});

test("sample state does not retain values from a previous real response", async () => {
  let requestCount = 0;
  const ui = createUi({
    get ok() { return requestCount++ === 0; },
    status: 503,
    json: async () => ({
      answer: "실제 응답",
      status: "answerable",
      facts: [{ company: "실제회사", metric: "revenue", raw_value: "999", raw_unit: "원" }],
      evidence: [],
    }),
  });
  ui.input.value = "첫 질문";
  await ui.form.listeners.submit({ preventDefault() {} });
  assert.equal(ui.elements["#company"].textContent, "실제회사");

  ui.input.value = "두 번째 질문";
  await ui.form.listeners.submit({ preventDefault() {} });
  await ui.elements["#sample-button"].listeners.click();

  assert.notEqual(ui.elements["#company"].textContent, "실제회사");
  assert.equal(ui.elements[".result-card"].dataset.mode, "sample");
});
