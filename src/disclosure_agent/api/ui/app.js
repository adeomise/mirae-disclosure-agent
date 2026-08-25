const form = document.querySelector("#question-form");
const input = document.querySelector("#question");
const submitButton = form.querySelector("button[type='submit']");
const resultCard = document.querySelector(".result-card");
const resultsSection = document.querySelector("#results");
const message = document.querySelector("#form-message");
const stateCard = document.querySelector("#state-card");
const stateIcon = document.querySelector(".state-icon");
const stateTitle = document.querySelector("#state-title");
const stateDescription = document.querySelector("#state-description");
const sampleButton = document.querySelector("#sample-button");

let lastFailedQuestion = "";

const fields = {
  company: document.querySelector("#company"),
  reportMeta: document.querySelector("#report-meta"),
  metricLabel: document.querySelector("#metric-label"),
  metricValue: document.querySelector("#metric-value"),
  metricChange: document.querySelector("#metric-change"),
  answer: document.querySelector("#answer-summary"),
  status: document.querySelector("#status-badge"),
  dataMode: document.querySelector("#data-mode"),
  evidenceCount: document.querySelector("#evidence-count"),
  evidenceList: document.querySelector("#evidence-list"),
};

const sampleView = {
  company: fields.company.textContent,
  reportMeta: fields.reportMeta.textContent,
  metricLabel: fields.metricLabel.textContent,
  metricValue: fields.metricValue.textContent,
  metricChange: fields.metricChange.textContent,
  evidenceCount: fields.evidenceCount.textContent,
  evidenceList: fields.evidenceList.innerHTML,
};

const escapeHtml = (value = "") =>
  String(value).replace(
    /[&<>'"]/g,
    (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character],
  );

const readableMetric = (metric) => {
  const labels = { revenue: "매출액", operating_income: "영업이익", net_income: "당기순이익" };
  return labels[metric] || metric || "분석 결과";
};

const displayValue = (fact) => {
  const value = fact?.normalized_value ?? fact?.raw_value;
  const unit = fact?.normalized_unit ?? fact?.raw_unit ?? "";
  return value === null || value === undefined ? "확인된 수치 없음" : `${value}${unit}`;
};

const showResultState = (mode) => {
  stateCard.hidden = true;
  resultCard.hidden = false;
  resultCard.dataset.mode = mode;
  fields.dataMode.hidden = false;
};

const showLoadingState = () => {
  resultsSection.hidden = false;
  resultsSection.classList.add("is-visible");
  resultCard.hidden = true;
  fields.dataMode.hidden = true;
  sampleButton.hidden = true;
  stateCard.hidden = false;
  stateCard.classList.remove("is-error");
  stateIcon.textContent = "…";
  stateTitle.textContent = "공시를 분석하고 있습니다";
  stateDescription.textContent = "Agent 응답을 확인하는 동안 잠시만 기다려 주세요.";
};

const showErrorState = (question) => {
  lastFailedQuestion = question;
  resultCard.hidden = true;
  resultCard.dataset.mode = "none";
  fields.dataMode.hidden = true;
  stateCard.hidden = false;
  stateCard.classList.add("is-error");
  stateIcon.textContent = "!";
  stateTitle.textContent = "현재 공시 Agent와 연결할 수 없습니다.";
  stateDescription.textContent = "잠시 후 다시 시도하거나, UI 확인용 샘플 결과를 직접 열어보세요.";
  sampleButton.hidden = false;
};

const renderEvidence = (evidence) => {
  const section = evidence.section_path?.join(" › ") || "문서 내 위치 정보 없음";
  const position = [evidence.row_label, evidence.column_label].filter(Boolean).join(" · ") || "—";
  const unit = evidence.provenance?.unit_declaration || evidence.provenance?.unit_source || "원문 참조";

  return `
    <article class="evidence-item">
      <div class="document-icon" aria-hidden="true"></div>
      <div class="evidence-content">
        <p class="document-name">${escapeHtml(evidence.doc_id)} <span>검증된 근거</span></p>
        <p class="document-path">${escapeHtml(section)}</p>
        <dl>
          <div><dt>위치</dt><dd>${escapeHtml(position)}</dd></div>
          <div><dt>단위</dt><dd>${escapeHtml(unit)}</dd></div>
        </dl>
      </div>
      <button class="source-button" type="button" disabled title="원문 URL 연결 전입니다">원문 보기</button>
    </article>`;
};

const renderResponse = (response) => {
  const fact = response.facts?.[0];
  fields.company.textContent = fact?.company || "공시 분석";
  fields.reportMeta.textContent = [fact?.fiscal_year, fact?.fiscal_period, fact?.accounting_scope]
    .filter(Boolean)
    .join(" · ") || "응답 요약";
  fields.metricLabel.textContent = readableMetric(fact?.metric);
  fields.metricValue.textContent = displayValue(fact);
  fields.metricChange.textContent = "Agent 응답에서 확인된 값";
  fields.answer.textContent = response.answer;
  fields.status.textContent = response.status === "answerable" ? "근거 확인 완료" : "답변 범위 확인 필요";
  fields.status.hidden = false;
  fields.status.classList.add("is-live");
  fields.dataMode.textContent = "API 응답";
  fields.dataMode.classList.add("is-live");

  const evidence = response.evidence || [];
  fields.evidenceCount.textContent = `${evidence.length}개 근거`;
  fields.evidenceList.innerHTML = evidence.length
    ? evidence.map(renderEvidence).join("")
    : '<p class="document-path">연결된 근거가 없습니다.</p>';
  showResultState("real");
};

const showDemoResult = (question) => {
  fields.company.textContent = sampleView.company;
  fields.reportMeta.textContent = sampleView.reportMeta;
  fields.metricLabel.textContent = sampleView.metricLabel;
  fields.metricValue.textContent = sampleView.metricValue;
  fields.metricChange.textContent = sampleView.metricChange;
  fields.evidenceCount.textContent = sampleView.evidenceCount;
  fields.evidenceList.innerHTML = sampleView.evidenceList;
  fields.answer.textContent = `“${question}” 질문을 받았습니다. 현재 Agent가 연결되지 않아 UI 확인용 가상 결과를 표시합니다.`;
  fields.dataMode.textContent = "SAMPLE · UI 확인용 데이터";
  fields.dataMode.classList.remove("is-live");
  fields.status.hidden = true;
  fields.status.classList.remove("is-live");
  showResultState("sample");
};

sampleButton.addEventListener("click", () => {
  showDemoResult(lastFailedQuestion || input.value.trim() || "샘플 질문");
  message.textContent = "";
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.question;
    input.focus();
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = input.value.trim();
  if (!question) return;

  submitButton.disabled = true;
  submitButton.textContent = "분석 중";
  resultCard.setAttribute("aria-busy", "true");
  message.textContent = "공시 응답을 확인하고 있습니다.";
  showLoadingState();

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });

    if (!response.ok) throw new Error(`API unavailable: ${response.status}`);
    renderResponse(await response.json());
    message.textContent = "Agent 응답을 불러왔습니다.";
  } catch (_error) {
    showErrorState(question);
    message.textContent = "현재 공시 Agent와 연결할 수 없습니다.";
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "질문하기";
    resultCard.setAttribute("aria-busy", "false");
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }
});
