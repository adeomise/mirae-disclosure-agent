const form = document.querySelector("#question-form");
const input = document.querySelector("#question");
const submitButton = form.querySelector("button[type='submit']");

const resultsSection = document.querySelector("#results");
const resultCard = document.querySelector("#result-card");

const message = document.querySelector("#form-message");

const stateCard = document.querySelector("#state-card");
const stateIcon = document.querySelector(".state-icon");
const stateTitle = document.querySelector("#state-title");
const stateDescription = document.querySelector("#state-description");

const fields = {
  status: document.querySelector("#status-badge"),
  reason: document.querySelector("#reason-message"),
  answer: document.querySelector("#answer-summary"),

  factsSection: document.querySelector("#facts-section"),
  factsCount: document.querySelector("#facts-count"),
  factsGrid: document.querySelector("#facts-grid"),

  calculationsSection: document.querySelector("#calculations-section"),
  calculationsList: document.querySelector("#calculations-list"),

  comparisonsSection: document.querySelector("#comparisons-section"),
  comparisonsList: document.querySelector("#comparisons-list"),

  eventsSection: document.querySelector("#events-section"),
  eventsList: document.querySelector("#events-list"),

  resolvedStatesSection: document.querySelector("#resolved-states-section"),
  resolvedStatesList: document.querySelector("#resolved-states-list"),

  historiesSection: document.querySelector("#histories-section"),
  historiesList: document.querySelector("#histories-list"),

  summariesSection: document.querySelector("#summaries-section"),
  summariesList: document.querySelector("#summaries-list"),

  narrativesSection: document.querySelector("#narratives-section"),
  narrativesList: document.querySelector("#narratives-list"),

  warningsSection: document.querySelector("#warnings-section"),
  warningsList: document.querySelector("#warnings-list"),

  evidencePanel: document.querySelector("#evidence-panel"),
  evidenceCount: document.querySelector("#evidence-count"),
  evidenceList: document.querySelector("#evidence-list"),
};


const STATUS_META = {
  ANSWERABLE: {
    label: "답변 가능",
    description: "",
  },
  PARTIALLY_ANSWERABLE: {
    label: "일부 답변 가능",
    description: "공시에서 확인할 수 있는 범위까지만 답변했습니다.",
  },
  UNRESOLVED: {
    label: "대상 확인 필요",
    description: "질문의 대상을 하나로 확정하지 못했습니다.",
  },
  UNANSWERABLE: {
    label: "답변 불가",
    description: "현재 공시 근거만으로는 답변하기 어렵습니다.",
  },
};


const REASON_LABELS = {
  incomplete_chain: "정정공시 체인이 완전하지 않습니다.",
  ambiguous_link: "관련 공시 연결 대상을 하나로 확정하지 못했습니다.",
  ambiguous_match: "질문에 해당하는 대상을 하나로 확정하지 못했습니다.",
  not_found: "질문에 해당하는 공시 정보를 찾지 못했습니다.",
  insufficient_evidence: "답변을 뒷받침할 충분한 근거를 확보하지 못했습니다.",
  withheld_in_source: "필요한 값이 원문 공시에서 공개되지 않았습니다.",
  partial_source_support: "질문의 일부 항목에 대해서만 공시 근거를 확보했습니다.",
  not_comparable: "확보한 값들을 같은 기준으로 직접 비교할 수 없습니다.",
  out_of_data_range: "현재 공시 데이터 범위를 벗어난 질문입니다.",
  out_of_scope_data: "현재 Agent가 사용하는 공시 데이터 범위를 벗어났습니다.",
  out_of_scope_policy: "현재 Agent가 답변하도록 허용된 범위를 벗어난 질문입니다.",
};


const METRIC_LABELS = {
  revenue: "매출액",
  operating_income: "영업이익",
  net_income: "당기순이익",

  holding_shares: "보유주식 수",
  holding_ratio: "보유비율",

  contract_amount: "계약금액",
  contract_start_date: "계약 시작일",
  contract_end_date: "계약 종료일",

  investment_amount: "투자금액",
  investment_start_date: "투자 시작일",
  investment_end_date: "투자 종료일",
};


const FIELD_LABELS = {
  contract_amount: "계약금액",
  contract_start_date: "계약 시작일",
  contract_end_date: "계약 종료일",

  investment_amount: "투자금액",
  investment_start_date: "투자 시작일",
  investment_end_date: "투자 종료일",
  board_resolution_date: "이사회결의일",

  counterparty: "계약상대방",
  contract_name: "계약명",
  contract_type: "계약유형",

  investment_type: "투자구분",
  investment_target: "투자대상",
  investment_purpose: "투자목적",
};


const UNIT_LABELS = {
  KRW: "원",
  won: "원",
  shares: "주",
  percent: "%",
};

const ACCOUNTING_SCOPE_LABELS = {
  consolidated: "연결",
  separate: "별도",
  standalone: "별도",
};


const escapeHtml = (value = "") =>
  String(value).replace(
    /[&<>'"]/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "'": "&#39;",
        '"': "&quot;",
      })[character],
  );


const list = (value) => Array.isArray(value) ? value : [];


const readableMetric = (metric) =>
  METRIC_LABELS[metric] || metric || "확인된 값";


const readableField = (field) =>
  FIELD_LABELS[field] || readableMetric(field);


const readableUnit = (unit) =>
  UNIT_LABELS[unit] || unit || "";


const formatNumber = (value) => {
  if (typeof value === "number") {
    return value.toLocaleString("ko-KR");
  }

  if (
    typeof value === "string" &&
    /^-?\d+(?:\.\d+)?$/.test(value.trim())
  ) {
    const numeric = Number(value);

    if (Number.isFinite(numeric)) {
      return numeric.toLocaleString("ko-KR", {
        maximumFractionDigits: 8,
      });
    }
  }

  return String(value ?? "");
};


const formatValue = (value, unit = "") => {
  if (value === null || value === undefined || value === "") {
    return "확인된 값 없음";
  }

  const formattedValue = formatNumber(value);
  const formattedUnit = readableUnit(unit);

  if (!formattedUnit) {
    return formattedValue;
  }

  if (formattedUnit === "%" || formattedUnit === "주" || formattedUnit === "원") {
    return `${formattedValue}${formattedUnit}`;
  }

  return `${formattedValue} ${formattedUnit}`;
};


const factValue = (fact) =>
  formatValue(
    fact?.normalized_value ?? fact?.raw_value,
    fact?.normalized_unit ?? fact?.raw_unit,
  );


const setSection = (section, container, html) => {
  if (!html) {
    section.hidden = true;
    container.innerHTML = "";
    return;
  }

  container.innerHTML = html;
  section.hidden = false;
};


const resetResult = () => {
  fields.reason.hidden = true;
  fields.reason.textContent = "";

  const sections = [
    [fields.factsSection, fields.factsGrid],
    [fields.calculationsSection, fields.calculationsList],
    [fields.comparisonsSection, fields.comparisonsList],
    [fields.eventsSection, fields.eventsList],
    [fields.resolvedStatesSection, fields.resolvedStatesList],
    [fields.historiesSection, fields.historiesList],
    [fields.summariesSection, fields.summariesList],
    [fields.narrativesSection, fields.narrativesList],
    [fields.warningsSection, fields.warningsList],
    [fields.evidencePanel, fields.evidenceList],
  ];

  sections.forEach(([section, container]) => {
    section.hidden = true;
    container.innerHTML = "";
  });
};


const showLoadingState = () => {
  resultsSection.hidden = false;
  resultsSection.classList.add("is-visible");

  resultCard.hidden = true;
  stateCard.hidden = false;
  stateCard.className = "state-card";

  stateIcon.textContent = "…";
  stateTitle.textContent = "공시를 분석하고 있습니다";
  stateDescription.textContent =
    "관련 공시와 근거를 확인하는 동안 잠시만 기다려 주세요.";
};


const showExecutionState = ({
  icon = "!",
  title,
  description,
  mode = "error",
}) => {
  resultsSection.hidden = false;
  resultCard.hidden = true;
  stateCard.hidden = false;

  stateCard.className = `state-card is-${mode}`;

  stateIcon.textContent = icon;
  stateTitle.textContent = title;
  stateDescription.textContent = description;
};


const renderFacts = (facts) => {
  const items = list(facts);

  fields.factsCount.textContent = `${items.length}개`;

  const html = items
    .map((fact) => {
      const accountingScope =
        ACCOUNTING_SCOPE_LABELS[fact.accounting_scope] || "";

      const meta = [
        fact.fiscal_year ? `${fact.fiscal_year}년` : "",
        fact.fiscal_period || "",
        accountingScope,
      ]
        .filter(Boolean)
        .join(" · ");

      return `
        <article class="fact-card">
          <div class="fact-card-top">
            <p class="fact-company">${escapeHtml(fact.company || "기업")}</p>
            <p class="fact-meta">${escapeHtml(meta)}</p>
          </div>

          <p class="fact-label">
            ${escapeHtml(readableMetric(fact.metric))}
          </p>

          <p class="fact-value">
            ${escapeHtml(factValue(fact))}
          </p>
        </article>
      `;
    })
    .join("");

  setSection(fields.factsSection, fields.factsGrid, html);
};


const renderCalculations = (calculations) => {
  const html = list(calculations)
    .map(
      (calculation) => `
        <article class="detail-card">
          <p class="detail-card-label">
            ${escapeHtml(calculation.operation || "계산")}
          </p>

          <p class="detail-card-value">
            ${escapeHtml(formatValue(calculation.value, calculation.unit))}
          </p>

          <p class="detail-card-meta">
            ${list(calculation.inputs).length}개 입력값을 이용한 계산
          </p>
        </article>
      `,
    )
    .join("");

  setSection(
    fields.calculationsSection,
    fields.calculationsList,
    html,
  );
};


const buildResultIndex = (response) => {
  const index = new Map();

  list(response.facts).forEach((fact) => {
    index.set(fact.fact_id, {
      type: "fact",
      label: `${fact.company} ${readableMetric(fact.metric)}`,
      value: factValue(fact),
    });
  });

  list(response.calculations).forEach((calculation) => {
    index.set(calculation.calculation_id, {
      type: "calculation",
      label: calculation.operation,
      value: formatValue(calculation.value, calculation.unit),
    });
  });

  return index;
};


const renderComparisons = (comparisons, response) => {
  const index = buildResultIndex(response);

  const relationLabels = {
    left_greater: ">",
    right_greater: "<",
    equal: "=",
  };

  const html = list(comparisons)
    .map((comparison) => {
      const left = index.get(comparison.left?.input_id);
      const right = index.get(comparison.right?.input_id);

      return `
        <article class="comparison-card">
          <div class="comparison-side">
            <p>${escapeHtml(left?.label || comparison.left?.input_id || "왼쪽 값")}</p>
            <strong>${escapeHtml(left?.value || "—")}</strong>
          </div>

          <span class="comparison-relation">
            ${escapeHtml(relationLabels[comparison.relation] || comparison.relation || "↔")}
          </span>

          <div class="comparison-side">
            <p>${escapeHtml(right?.label || comparison.right?.input_id || "오른쪽 값")}</p>
            <strong>${escapeHtml(right?.value || "—")}</strong>
          </div>
        </article>
      `;
    })
    .join("");

  setSection(
    fields.comparisonsSection,
    fields.comparisonsList,
    html,
  );
};


const renderEvents = (events) => {
  const html = list(events)
    .map((event) => {
      const attributes = Object.entries(event.attributes || {})
        .map(
          ([key, value]) => `
            <div class="key-value-row">
              <span>${escapeHtml(readableField(key))}</span>
              <strong>${escapeHtml(value ?? "—")}</strong>
            </div>
          `,
        )
        .join("");

      return `
        <article class="detail-card">
          <div class="detail-card-heading">
            <div>
              <p class="detail-card-label">
                ${escapeHtml(event.company || "기업")}
              </p>
              <p class="detail-card-meta">
                ${escapeHtml(event.event_type || "이벤트 공시")}
              </p>
            </div>

            ${
              event.is_correction
                ? '<span class="mini-badge">정정</span>'
                : ""
            }
          </div>

          <div class="key-value-list">
            ${attributes}
          </div>
        </article>
      `;
    })
    .join("");

  setSection(fields.eventsSection, fields.eventsList, html);
};


const renderResolvedStates = (states) => {
  const html = list(states)
    .map((state) => {
      const rows = Object.entries(state.fields || {})
        .map(([name, field]) => {
          return `
            <div class="key-value-row">
              <span>${escapeHtml(readableField(name))}</span>
              <strong>
                ${escapeHtml(formatValue(field.value, field.unit))}
              </strong>
            </div>
          `;
        })
        .join("");

      return `
        <article class="detail-card">
          <div class="detail-card-heading">
            <div>
              <p class="detail-card-label">
                ${escapeHtml(state.company || "기업")}
              </p>
              <p class="detail-card-meta">
                ${escapeHtml(state.event_type || "")}
                ${state.as_of ? ` · ${escapeHtml(state.as_of)} 기준` : ""}
              </p>
            </div>
          </div>

          <div class="key-value-list">
            ${rows}
          </div>
        </article>
      `;
    })
    .join("");

  setSection(
    fields.resolvedStatesSection,
    fields.resolvedStatesList,
    html,
  );
};


const renderHistories = (histories) => {
  const html = list(histories)
    .map((history) => {
      const changes = list(history.changes)
        .map(
          (change) => `
            <div class="change-row">
              <p class="change-field">
                ${escapeHtml(readableField(change.field_name))}
              </p>

              <div class="change-values">
                <span>
                  ${escapeHtml(formatValue(change.before, change.unit))}
                </span>

                <span class="change-arrow" aria-hidden="true">→</span>

                <strong>
                  ${escapeHtml(formatValue(change.after, change.unit))}
                </strong>
              </div>
            </div>
          `,
        )
        .join("");

      return `
        <article class="detail-card">
          <div class="detail-card-heading">
            <div>
              <p class="detail-card-label">정정공시 이력</p>
              <p class="detail-card-meta">
                ${list(history.document_ids).length}개 문서
              </p>
            </div>

            <span class="mini-badge">
              ${history.chain_complete ? "체인 확인 완료" : "일부 확인"}
            </span>
          </div>

          <div class="change-list">
            ${changes || '<p class="empty-copy">확인된 변경 항목이 없습니다.</p>'}
          </div>
        </article>
      `;
    })
    .join("");

  setSection(
    fields.historiesSection,
    fields.historiesList,
    html,
  );
};


const renderSummaries = (summaries) => {
  const html = list(summaries)
    .map((summary) => {
      const items = list(summary.items)
        .map(
          (item) => `
            <li>
              ${escapeHtml(item.label)}
            </li>
          `,
        )
        .join("");

      return `
        <article class="detail-card">
          <p class="detail-card-label">
            ${escapeHtml(summary.company || "기업")}
          </p>

          <p class="detail-card-meta">
            ${escapeHtml(summary.report_name || "")}
          </p>

          <ul class="summary-items">
            ${items}
          </ul>
        </article>
      `;
    })
    .join("");

  setSection(
    fields.summariesSection,
    fields.summariesList,
    html,
  );
};


const renderNarratives = (comparisons) => {
  const html = list(comparisons)
    .map((comparison) => {
      const claims = list(comparison.claims)
        .map(
          (claim) => `
            <li>${escapeHtml(claim.label)}</li>
          `,
        )
        .join("");

      return `
        <article class="detail-card">
          <p class="detail-card-label">
            ${escapeHtml(comparison.company || "기업")}
          </p>

          <p class="detail-card-meta">
            ${escapeHtml(comparison.left_fiscal_year)}
            →
            ${escapeHtml(comparison.right_fiscal_year)}
          </p>

          ${
            claims
              ? `<ul class="summary-items">${claims}</ul>`
              : '<p class="empty-copy">의미 있는 변화가 확인되지 않았습니다.</p>'
          }
        </article>
      `;
    })
    .join("");

  setSection(
    fields.narrativesSection,
    fields.narrativesList,
    html,
  );
};


const renderWarnings = (warnings) => {
  const html = list(warnings)
    .map((warning) => `<li>${escapeHtml(warning)}</li>`)
    .join("");

  setSection(
    fields.warningsSection,
    fields.warningsList,
    html,
  );
};


const renderEvidence = (evidence) => {
  const items = list(evidence);

  fields.evidenceCount.textContent = `${items.length}개 근거`;

  const html = items
    .map((item) => {
      const section =
        list(item.section_path).join(" › ") ||
        item.table_name ||
        "문서 내 위치";

      const position = [
        item.row_label,
        item.column_label,
        item.field_name,
      ]
        .filter(Boolean)
        .join(" · ");

      const unit =
        item.provenance?.unit_declaration ||
        item.provenance?.unit_source ||
        "";

      return `
        <article class="evidence-item">
          <div class="document-icon" aria-hidden="true"></div>

          <div class="evidence-content">
            <p class="document-name">
              ${escapeHtml(item.doc_id)}
            </p>

            <p class="document-path">
              ${escapeHtml(section)}
            </p>

            ${
              item.raw_text
                ? `<p class="evidence-quote">${escapeHtml(item.raw_text)}</p>`
                : ""
            }

            <dl>
              ${
                position
                  ? `
                    <div>
                      <dt>위치</dt>
                      <dd>${escapeHtml(position)}</dd>
                    </div>
                  `
                  : ""
              }

              ${
                unit
                  ? `
                    <div>
                      <dt>단위</dt>
                      <dd>${escapeHtml(unit)}</dd>
                    </div>
                  `
                  : ""
              }
            </dl>
          </div>
        </article>
      `;
    })
    .join("");

  setSection(
    fields.evidencePanel,
    fields.evidenceList,
    html,
  );
};


const renderResponse = (response) => {
  resetResult();

  const status = response.status || "UNANSWERABLE";
  const statusMeta =
    STATUS_META[status] || {
      label: status,
      description: "",
    };

  fields.status.textContent = statusMeta.label;
  fields.answer.textContent =
    cleanAnswerText(response.answer) ||
    "답변 문장이 제공되지 않았습니다.";

  resultCard.dataset.status = status;

  if (response.reason) {
    fields.reason.textContent =
      REASON_LABELS[response.reason] ||
      statusMeta.description ||
      response.reason;

    fields.reason.hidden = false;
  } else if (statusMeta.description) {
    fields.reason.textContent = statusMeta.description;
    fields.reason.hidden = false;
  }

  renderFacts(response.facts);
  renderCalculations(response.calculations);
  renderComparisons(response.comparisons, response);
  renderEvents(response.events);
  renderResolvedStates(response.resolved_event_states);
  renderHistories(response.event_histories);
  renderSummaries(response.document_summaries);
  renderNarratives(response.narrative_comparisons);
  renderWarnings(response.warnings);
  renderEvidence(response.evidence);

  stateCard.hidden = true;
  resultCard.hidden = false;
};


const handleHttpError = (response, payload) => {
  if (
    response.status === 422 &&
    payload?.execution_status === "BLOCKED"
  ) {
    showExecutionState({
      icon: "!",
      title: "질문을 현재 조건으로 처리할 수 없습니다",
      description:
        payload.error?.message ||
        "질문을 조금 더 구체적으로 입력해 주세요.",
      mode: "blocked",
    });

    return;
  }

  if (
    response.status === 422 &&
    Array.isArray(payload?.detail)
  ) {
    showExecutionState({
      icon: "!",
      title: "질문 형식을 확인해 주세요",
      description:
        "입력값을 확인한 뒤 다시 질문해 주세요.",
      mode: "blocked",
    });

    return;
  }

  showExecutionState({
    icon: "!",
    title: "요청 처리 중 오류가 발생했습니다",
    description:
      payload?.error?.message ||
      "잠시 후 다시 시도해 주세요.",
    mode: "error",
  });
};


document
  .querySelectorAll("[data-question]")
  .forEach((button) => {
    button.addEventListener("click", () => {
      input.value = button.dataset.question;
      input.focus();
    });
  });


form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const question = input.value.trim();

  if (!question) {
    return;
  }

  submitButton.disabled = true;
  submitButton.textContent = "분석 중";

  resultCard.setAttribute("aria-busy", "true");

  message.textContent = "관련 공시를 확인하고 있습니다.";

  showLoadingState();

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ question }),
    });

    let payload = null;

    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }

    if (!response.ok) {
      handleHttpError(response, payload);
      message.textContent = "";
      return;
    }

    renderResponse(payload);

    message.textContent = "";
  } catch (_error) {
    showExecutionState({
      icon: "!",
      title: "공시 Agent에 연결할 수 없습니다",
      description:
        "서버 연결 상태를 확인한 뒤 다시 시도해 주세요.",
      mode: "error",
    });

    message.textContent = "";
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "질문하기";

    resultCard.setAttribute("aria-busy", "false");

    resultsSection.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }
});

const EXECUTION_REASON_LABELS = {
  ambiguous_entity: {
    title: "기업을 하나로 특정할 수 없습니다",
    description:
      "기업명을 조금 더 정확하게 입력해 주세요. 예: 삼성전자, 삼성바이오로직스",
  },

  ambiguous_match: {
    title: "질문의 대상을 하나로 특정할 수 없습니다",
    description:
      "기업명이나 공시 대상을 조금 더 구체적으로 입력해 주세요.",
  },

  ambiguous_filing: {
    title: "대상 공시를 하나로 특정할 수 없습니다",
    description:
      "연도나 공시 종류 등 조건을 조금 더 구체적으로 입력해 주세요.",
  },

  no_matching_filing: {
    title: "조건에 맞는 공시를 찾지 못했습니다",
    description:
      "기업명, 연도 또는 공시 종류를 확인한 뒤 다시 질문해 주세요.",
  },

  unsupported_event_type: {
    title: "현재 지원하지 않는 공시 유형입니다",
    description:
      "지원되는 공시 유형으로 다시 질문해 주세요.",
  },
};

const cleanAnswerText = (answer = "") =>
  String(answer)
    .replace(/\s*\(정규화\s*[\d,]+원\)/g, "")
    .trim();