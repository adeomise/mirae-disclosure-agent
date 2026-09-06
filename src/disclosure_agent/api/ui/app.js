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
const stateSuggestions = document.querySelector("#state-suggestions");

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


// 소수점 둘째 자리로 고정 표기한다(12.3 → "12.30", 12.345 → "12.35"). "%" 단위 값
// 전용이며, 다른 단위는 formatNumber를 그대로 쓴다.
const formatPercent = (value) => {
  const numeric = typeof value === "number" ? value : Number(value);

  if (!Number.isFinite(numeric)) {
    return String(value ?? "");
  }

  return numeric.toLocaleString("ko-KR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
};


const formatValue = (value, unit = "") => {
  if (value === null || value === undefined || value === "") {
    return "확인된 값 없음";
  }

  const formattedUnit = readableUnit(unit);
  const formattedValue =
    formattedUnit === "%" ? formatPercent(value) : formatNumber(value);

  if (!formattedUnit) {
    return formattedValue;
  }

  if (formattedUnit === "%" || formattedUnit === "주" || formattedUnit === "원") {
    return `${formattedValue}${formattedUnit}`;
  }

  return `${formattedValue} ${formattedUnit}`;
};


// "17,580,633,257,000" 같은 정확한 값을 "17조 5,806억" 형태로 보조 표기한다. 조/억
// 단위로만 나누고 억 미만은 절사한다("약"이 붙으므로 반올림하지 않는다). 1억 미만
// 소액은 보조 표기가 의미 없으므로 null을 돌려준다(호출부는 이때 보조 줄을 붙이지
// 않는다).
const formatMoneyKorean = (value) => {
  const numeric = typeof value === "number" ? value : Number(value);

  if (!Number.isFinite(numeric)) {
    return null;
  }

  const sign = numeric < 0 ? "-" : "";
  const absValue = Math.abs(numeric);

  const trillion = Math.floor(absValue / 1e12);
  const remainder = absValue - trillion * 1e12;
  const eok = Math.floor(remainder / 1e8);

  const parts = [];

  if (trillion > 0) {
    parts.push(`${trillion.toLocaleString("ko-KR")}조`);
  }

  if (eok > 0) {
    parts.push(`${eok.toLocaleString("ko-KR")}억`);
  }

  if (!parts.length) {
    return null;
  }

  return sign + parts.join(" ");
};


// 정확한 값과, 원화일 때만 붙는 "약 N조 N억원" 보조 표기를 함께 돌려준다. 이 보조
// 표기는 normalized_value + normalized_unit에만 적용한다 — raw_value/raw_text는
// 원문 그대로 유지해야 하므로 호출부에서 normalized 값일 때만 이 함수를 쓴다.
const formatValueWithReadable = (value, unit = "") => {
  const exact = formatValue(value, unit);
  const formattedUnit = readableUnit(unit);

  if (formattedUnit !== "원") {
    return { exact, readable: null };
  }

  const numeric = typeof value === "number" ? value : Number(value);

  if (!Number.isFinite(numeric)) {
    return { exact, readable: null };
  }

  const readableAmount = formatMoneyKorean(numeric);

  return {
    exact,
    readable: readableAmount ? `약 ${readableAmount}원` : null,
  };
};


// formatValueWithReadable의 결과를 안전하게 이스케이프한 HTML 마크업으로 만든다.
// 보조 표기가 있으면 별도 <span class="value-readable">로 감싸 두 줄로 보이게 한다.
const valueMarkup = (value, unit = "") => {
  const { exact, readable } = formatValueWithReadable(value, unit);
  const readableHtml = readable
    ? `<span class="value-readable">${escapeHtml(readable)}</span>`
    : "";

  return `${escapeHtml(exact)}${readableHtml}`;
};


// normalized_value가 있으면 그 값에만 "약 N조 N억원" 보조 표기를 적용한다. raw_value로
// 대체 표시하는 경우(정규화 실패 등)는 원문 값이므로 보조 표기를 붙이지 않는다.
const factValue = (fact) => {
  const hasNormalized =
    fact?.normalized_value !== null &&
    fact?.normalized_value !== undefined &&
    fact?.normalized_value !== "";

  if (hasNormalized) {
    return valueMarkup(fact.normalized_value, fact.normalized_unit);
  }

  return escapeHtml(formatValue(fact?.raw_value, fact?.raw_unit));
};


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


// BLOCKED 응답이 payload.error.details.allowed_report_types 같은 배열을 내려줄 때만
// pill 버튼으로 나열한다. 배열이 없으면 아무것도 그리지 않는다 — UI가 임의로 보고서
// 종류를 하드코딩해서 항상 보여주지 않는다.
const renderStateSuggestions = (suggestions) => {
  const items = list(suggestions).filter(
    (value) => typeof value === "string" && value.trim(),
  );

  if (!items.length) {
    stateSuggestions.hidden = true;
    stateSuggestions.innerHTML = "";
    return;
  }

  stateSuggestions.innerHTML = items
    .map(
      (value) => `
        <button type="button" data-question-fill="${escapeHtml(value)}">
          ${escapeHtml(value)}
        </button>
      `,
    )
    .join("");

  stateSuggestions.hidden = false;

  // 후보 버튼은 "상반기 보고서"·"반기보고서"처럼 백엔드가 구분해 둔 보고서 종류
  // 문자열일 뿐, UI는 그 의미(상반기/하반기 등)를 스스로 해석하지 않는다. 그래서
  // 기존 질문을 지우고 그 문자열로 통째로 바꾸는 대신, 사용자가 이미 입력한 질문
  // 뒤에 선택값을 안전하게 덧붙이기만 한다 — 질문 문장 구조를 UI가 재구성하려 하면
  // 원래 의도(회사명·연도 등)를 깨뜨릴 수 있기 때문이다.
  stateSuggestions
    .querySelectorAll("[data-question-fill]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        const current = input.value.trim();
        const addition = button.dataset.questionFill;

        if (!current) {
          input.value = addition;
        } else if (!current.includes(addition)) {
          input.value = `${current} ${addition}`;
        }

        input.focus();
      });
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

  renderStateSuggestions([]);
};


const showExecutionState = ({
  icon = "!",
  title,
  description,
  mode = "error",
  suggestions = [],
}) => {
  resultsSection.hidden = false;
  resultCard.hidden = true;
  stateCard.hidden = false;

  stateCard.className = `state-card is-${mode}`;

  stateIcon.textContent = icon;
  stateTitle.textContent = title;
  stateDescription.textContent = description;

  renderStateSuggestions(suggestions);
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
            ${factValue(fact)}
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
            ${valueMarkup(calculation.value, calculation.unit)}
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
      value: valueMarkup(calculation.value, calculation.unit),
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
            <strong>${left?.value || "—"}</strong>
          </div>

          <span class="comparison-relation">
            ${escapeHtml(relationLabels[comparison.relation] || comparison.relation || "↔")}
          </span>

          <div class="comparison-side">
            <p>${escapeHtml(right?.label || comparison.right?.input_id || "오른쪽 값")}</p>
            <strong>${right?.value || "—"}</strong>
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
                ${valueMarkup(field.value, field.unit)}
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
                  ${valueMarkup(change.before, change.unit)}
                </span>

                <span class="change-arrow" aria-hidden="true">→</span>

                <strong>
                  ${valueMarkup(change.after, change.unit)}
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


// 백엔드가 표/셀 위치를 추적하려고 row_label/column_label/field_name 등에 심어 두는
// 내부 provenance 문자열("table_id=table_0127;row_index=5;col_index=1")을 걸러낸다.
// 이런 값은 화면에 그대로 보여줄 게 아니라 완전히 감춰야 한다.
const looksLikeRawProvenance = (value) =>
  typeof value === "string" &&
  /(table_id|row_index|col_index)\s*=/.test(value);


// 재무제표 원문의 row_label/field_name에는 "매출액(주30)"처럼 주석 번호가 붙어 있을
// 때가 있다. "주30"은 자산 수량이 아니라 재무제표 하단 주석 제30번을 가리키는 참조
// 표기라 사용자용 위치 라벨에서는 의미가 없고 오히려 "수량 30"처럼 오해를 준다.
// 두 형태만 좁게 제거한다 — 괄호로 감싼 "(주30)"·"(주 12)"와, 값 끝에 단독으로 붙은
// "주30". "수익(매출액)"처럼 괄호 안에 "주"+숫자가 아닌 내용이 있으면 손대지 않는다.
// raw_text/evidence 원문에는 적용하지 않고, 위치 라벨(row_label/field_name)에만 쓴다.
const stripFootnoteReference = (value) => {
  if (typeof value !== "string") {
    return value;
  }

  return value
    .replace(/\s*\(\s*주\s*\d{1,3}\s*\)/g, "")
    .replace(/(^|\s)주\s?\d{1,3}$/, "$1")
    .trim();
};


// fiscal_period로 표현 가능한 값들을 실제 코드에서 확인한 대로 매핑한다.
//
// - "quarter" / "half-year" / "annual": periodic/extractor.py의 infer_period가
//   만드는 값. Q1/Q3를 구분하는 정보가 infer_period 자체에 없으므로 "quarter"는
//   분기 구분 없이 "분기보고서"로만 표기한다(1분기/3분기를 UI가 지어내지 않는다).
// - "FY": common/schemas.py 계약 테스트(FULL_YEAR_FISCAL_PERIODS)가 쓰는 연간 표기.
//
// 실제 프로덕션 응답(단일 Fact 조회 Pattern A: periodic/adapter.py의
// adapt_financial_fact)은 fiscal_period를 항상 None으로 채운다 — 파싱된 원문
// metadata.report_name/기간 정보가 있어도 Fact/Evidence Contract로 옮겨지지
// 않는다. 그래서 이 표는 document_summaries에도 없고 fiscal_period도 없는
// 대부분의 단일 수치 조회에는 적용되지 않으며, 그 경우는 아래 documentLabel의
// "관련 공시 문서" 폴백으로 정직하게 떨어지는 것이 맞다(같은 연도라고 사업보고서로
// 추정하지 않는다).
const FISCAL_PERIOD_REPORT_LABELS = {
  quarter: "분기보고서",
  "half-year": "반기보고서",
  annual: "사업보고서",
  FY: "사업보고서",
};


// evidence_id -> { company, fiscalYear, fiscalPeriod, docId, reportName } 맵을 만든다.
// document_summaries가 가장 신뢰할 수 있는 출처(report_name을 직접 가짐)이고, 그 다음
// facts.evidence_ids를 통해 간접적으로 채운다. calculations는 자신의 evidence_ids를
// inputs가 가리키는 fact로부터 회사/연도 정보를 상속받는다. comparisons는 자체
// evidence_ids를 갖지 않는 스키마이므로(관련 fact/calculation을 통해서만 근거를 가짐)
// 여기서 직접 다루지 않는다.
const buildEvidenceContext = (response) => {
  const context = new Map();

  const summaryByDocId = new Map();
  list(response.document_summaries).forEach((summary) => {
    if (summary?.doc_id) {
      summaryByDocId.set(summary.doc_id, summary);
    }
  });

  const evidenceById = new Map();
  list(response.evidence).forEach((item) => {
    if (item?.evidence_id) {
      evidenceById.set(item.evidence_id, item);
    }
  });

  const applyContext = ({ evidenceIds, company, fiscalYear, fiscalPeriod }) => {
    list(evidenceIds).forEach((evidenceId) => {
      if (context.has(evidenceId)) {
        return;
      }

      const evidenceItem = evidenceById.get(evidenceId);
      const docId = evidenceItem?.doc_id;
      const summary = docId ? summaryByDocId.get(docId) : undefined;

      context.set(evidenceId, {
        company: summary?.company || company,
        fiscalYear,
        fiscalPeriod,
        docId,
        reportName: summary?.report_name,
      });
    });
  };

  list(response.facts).forEach((fact) => {
    applyContext({
      evidenceIds: fact.evidence_ids,
      company: fact.company,
      fiscalYear: fact.fiscal_year,
      fiscalPeriod: fact.fiscal_period,
    });
  });

  const factById = new Map();
  list(response.facts).forEach((fact) => {
    factById.set(fact.fact_id, fact);
  });

  list(response.calculations).forEach((calculation) => {
    const sourceFact = list(calculation.inputs)
      .filter((input) => input?.input_type === "fact")
      .map((input) => factById.get(input.input_id))
      .find(Boolean);

    if (sourceFact) {
      applyContext({
        evidenceIds: calculation.evidence_ids,
        company: sourceFact.company,
        fiscalYear: sourceFact.fiscal_year,
        fiscalPeriod: sourceFact.fiscal_period,
      });
    }
  });

  // fact/calculation 어디에도 연결되지 않았지만(예: Pattern B 요약형 응답) 같은
  // doc_id에 document_summaries가 있는 evidence는 그 정보만으로라도 채운다.
  list(response.evidence).forEach((item) => {
    if (context.has(item.evidence_id) || !item.doc_id) {
      return;
    }

    const summary = summaryByDocId.get(item.doc_id);

    if (summary) {
      context.set(item.evidence_id, {
        company: summary.company,
        fiscalYear: undefined,
        fiscalPeriod: undefined,
        docId: item.doc_id,
        reportName: summary.report_name,
      });
    }
  });

  return context;
};


// Evidence 카드 제목에 쓸 사람이 읽는 문서 라벨. doc_id를 그대로 노출하지 않는 것이
// 이 함수의 핵심 목적이다 — 우선순위: report_name > company+fiscalYear(+fiscalPeriod
// 매핑) > "관련 공시 문서" 폴백.
const documentLabel = (evidenceItem, context) => {
  const info = context.get(evidenceItem.evidence_id);

  if (info?.reportName) {
    const alreadyHasYear =
      info.fiscalYear !== undefined &&
      info.fiscalYear !== null &&
      info.reportName.includes(String(info.fiscalYear));

    const label =
      info.fiscalYear && !alreadyHasYear
        ? `${info.fiscalYear}년 ${info.reportName}`
        : info.reportName;

    return info.company ? `${info.company} ${label}` : label;
  }

  if (info?.company && info?.fiscalYear) {
    const periodLabel = FISCAL_PERIOD_REPORT_LABELS[info.fiscalPeriod] || "공시";

    return `${info.company} ${info.fiscalYear}년 ${periodLabel}`;
  }

  return "관련 공시 문서";
};


// AnswerResponse.source_documents(additive 필드)를 doc_id 기준 Map으로 만든다.
// Evidence.doc_id와 정확히 일치하는 항목만 찾아 쓰기 위한 색인이다 — 이 Map 자체는
// 추측을 하지 않으며, 백엔드가 내려준 SourceDocument를 그대로 담는다.
const buildSourceDocumentIndex = (response) => {
  const index = new Map();

  list(response.source_documents).forEach((doc) => {
    if (doc?.doc_id) {
      index.set(doc.doc_id, doc);
    }
  });

  return index;
};


// document_subtype + base_month로부터 사람이 읽는 보고서 종류를 만든다. 질문
// 문장이나 다른 필드로부터 절대 추측하지 않고, 실제 국내 정기공시 체계에서 확인된
// 조합만 exact allowlist로 등록한다 — "quarter"의 분기 번호를 base_month로부터
// 계산(Math.ceil(base_month / 3) 등)하지 않는다. 예를 들어 "quarter + base_month=6"은
// 실제로 존재하지 않는 조합(2분기만 단독 보고서로 나오지 않음)인데 계산식은 이를
// "2분기보고서"로 만들어낼 수 있으므로 금지한다. allowlist에 없는 조합이거나
// base_month가 숫자가 아니면 null을 돌려줘 호출부가 report_name/기존 fallback으로
// 넘어가게 한다.
const DOCUMENT_SUBTYPE_BASE_MONTH_LABELS = new Map([
  ["annual:12", "사업보고서"],
  ["half:6", "반기보고서"],
  ["quarter:3", "1분기보고서"],
  ["quarter:9", "3분기보고서"],
]);

const subtypeReportLabel = (documentSubtype, baseMonth) => {
  if (
    typeof documentSubtype !== "string" ||
    typeof baseMonth !== "number" ||
    !Number.isFinite(baseMonth)
  ) {
    return null;
  }

  return DOCUMENT_SUBTYPE_BASE_MONTH_LABELS.get(`${documentSubtype}:${baseMonth}`) || null;
};


// report_name 끝에 "(2025.12)"처럼 결산연도.월이 괄호로 붙어 있으면 그 연/월과
// 괄호를 뗀 본문을 함께 돌려준다. 지원 범위는 기존과 동일하게 "끝에 붙은
// (YYYY.M[M])" 하나뿐이며 여기서 넓히지 않는다 — "(수정)"처럼 연도.월 형식이
// 아닌 괄호는 그 안에 무슨 내용이 있는지 모르므로 손대지 않고 null을 돌려준다.
// suffix가 없으면 null이고, 호출부는 report_name을 원문 그대로 쓴다.
const REPORT_NAME_DATE_SUFFIX_PATTERN = /\s*\(\s*(\d{4})\s*\.\s*(\d{1,2})\s*\)\s*$/;

const parseReportNameDateSuffix = (reportName) => {
  if (typeof reportName !== "string") {
    return null;
  }

  const match = reportName.match(REPORT_NAME_DATE_SUFFIX_PATTERN);

  if (!match) {
    return null;
  }

  return {
    year: Number(match[1]),
    month: Number(match[2]),
    bare: reportName.slice(0, match.index).trim(),
  };
};


// SourceDocument(신규 additive 필드) 하나로부터 사람이 읽는 문서 라벨을 만든다.
// 우선순위: report_name(있으면 document_subtype 매핑보다 항상 우선, 요구사항 5) >
// document_subtype+base_month exact allowlist. fiscal_year는 그 문구에 연도가 이미
// 포함되어 있지 않을 때만 앞에 붙여 중복 표시를 막는다 — 단, report_name의 날짜
// suffix("(2025.12)")를 떼어내는 것은 fiscal_year가 있고 그 suffix의 연도가
// fiscal_year와 정확히 같아서, 표준 "{fiscal_year}년 {report_name}" 형식이 원래
// 정보를 그대로 다시 쓴 것일 때만 한다. fiscal_year가 없으면 report_name을 원문
// 그대로 둔다. company는 있으면 맨 앞에 붙인다. report_name도 subtype 매핑도 없어
// phrase를 만들 수 없으면 null을 돌려줘, 호출부가 기존 fact/evidence context
// fallback으로 넘어가게 한다.
//
// fiscal_year=2025인데 report_name="사업보고서 (2024.12)"처럼 두 연도가 어긋나면
// backend metadata 자체가 충돌한 상태다. 이때 suffix를 떼면 UI가 그 충돌을 감추고
// "2025년 사업보고서"라는 실재 여부를 알 수 없는 문서명을 합성하게 되므로, 어느 한
// 쪽을 고르지 않고 SourceDocument 기반 라벨 생성 전체를 fail-close(null)한다 —
// 호출부(resolveDocumentLabel)가 기존 legacy documentLabel fallback으로 넘어간다.
const sourceDocumentLabel = (sourceDoc) => {
  if (!sourceDoc) {
    return null;
  }

  const { company, fiscal_year: fiscalYear, report_name: reportName } = sourceDoc;
  const hasFiscalYear = fiscalYear !== undefined && fiscalYear !== null;

  let reportNameBare = reportName || null;

  if (reportName && hasFiscalYear) {
    const dateSuffix = parseReportNameDateSuffix(reportName);

    if (dateSuffix) {
      if (String(dateSuffix.year) !== String(fiscalYear).trim()) {
        return null;
      }

      reportNameBare = dateSuffix.bare;
    }
  }

  const phrase =
    reportNameBare || subtypeReportLabel(sourceDoc.document_subtype, sourceDoc.base_month);

  if (!phrase) {
    return null;
  }

  const alreadyHasYear = hasFiscalYear && phrase.includes(String(fiscalYear));

  const withYear =
    hasFiscalYear && !alreadyHasYear ? `${fiscalYear}년 ${phrase}` : phrase;

  return company ? `${company} ${withYear}` : withYear;
};


// Evidence 카드 제목에 실제로 쓸 라벨. source_documents가 doc_id로 join되고 그
// metadata로 라벨을 만들 수 있으면 그 값을 최우선으로 쓴다(추측 없는 정확한 값).
// join이 실패하거나(doc_id가 source_documents에 없음) 라벨을 만들 필드가 부족하면
// 기존 fact/evidence context 기반 documentLabel로 fallback한다 — 이 fallback은
// 이미 periodic_* 원시 doc_id를 노출하지 않는다.
const resolveDocumentLabel = (evidenceItem, context, sourceDocumentIndex) => {
  const sourceDoc = evidenceItem?.doc_id
    ? sourceDocumentIndex?.get(evidenceItem.doc_id)
    : undefined;

  return sourceDocumentLabel(sourceDoc) || documentLabel(evidenceItem, context);
};


// SourceDocument.filing_date(백엔드 ISO "YYYY-MM-DD")를 화면 표기 "YYYY.MM.DD"로
// 바꾼다. 정확히 그 형식이고, 실제로 존재하는 달력 날짜일 때만 값을 돌려준다 —
// 그 외(누락, 형식 불일치, "2024-02-30"처럼 실재하지 않는 날짜)는 null을 돌려줘
// 호출부가 제출일 행 자체를 숨기게 한다. 오늘 날짜나 질문 속 연도로 추측하지 않는다.
const formatFilingDate = (isoDate) => {
  if (typeof isoDate !== "string") {
    return null;
  }

  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate.trim());

  if (!match) {
    return null;
  }

  const [, yearStr, monthStr, dayStr] = match;
  const year = Number(yearStr);
  const month = Number(monthStr);
  const day = Number(dayStr);

  // Date.UTC는 범위를 벗어난 월/일을 다음 달력으로 "정규화"한다(예: 2월 30일 →
  // 3월 1~2일). 정규화된 결과가 입력과 다르면 애초에 존재하지 않는 날짜였다는
  // 뜻이므로 그 경우를 걸러낸다.
  const date = new Date(Date.UTC(year, month - 1, day));

  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null;
  }

  return `${yearStr}.${monthStr}.${dayStr}`;
};


const renderEvidence = (evidence, context, sourceDocumentIndex) => {
  const items = list(evidence);

  fields.evidenceCount.textContent = `${items.length}개 근거`;

  const html = items
    .map((item) => {
      const section =
        list(item.section_path).join(" › ") ||
        item.table_name ||
        "문서 내 위치";

      // row_label은 표의 "행 이름"이라 값만 남으면 "OO 항목"처럼 자연스럽게 읽힌다.
      // column_label은 표의 "열 이름"이라 같은 접미사를 붙이면 어색해지므로("매출액
      // 항목" O, "2025년 3분기 항목" X) 원문 그대로 둔다. 둘 다에서 재무제표 주석
      // 번호("(주30)" 등)는 떼어내고, 뗀 결과가 빈 문자열이면 그 후보는 버린다.
      const rowLabel = stripFootnoteReference(item.row_label);
      const hasRowLabel = Boolean(rowLabel) && !looksLikeRawProvenance(rowLabel);

      const columnLabel = item.column_label;
      const hasColumnLabel =
        Boolean(columnLabel) && !looksLikeRawProvenance(columnLabel);

      // field_name은 investment_amount처럼 내부 snake_case 식별자라, row_label이나
      // column_label 같은 사람이 읽는 라벨이 하나라도 있으면 화면에서 완전히
      // 제외한다("투자금액(원) · investment_amount"처럼 나란히 보이면 안 된다).
      // 사람이 읽는 라벨이 전혀 없을 때만 field_name을 최후 후보로 보되, 그마저도
      // FIELD_LABELS에 명시적 매핑이 있는 값일 때만 쓴다 — 매핑이 없는 내부
      // 식별자를 그대로 노출하거나 임의로 번역하지 않는다.
      let position = "";

      if (hasRowLabel && hasColumnLabel) {
        position =
          rowLabel === columnLabel
            ? `${rowLabel} 항목`
            : `${rowLabel} · ${columnLabel}`;
      } else if (hasRowLabel) {
        position = `${rowLabel} 항목`;
      } else if (hasColumnLabel) {
        position = columnLabel;
      } else {
        const fieldName = stripFootnoteReference(item.field_name);
        const mappedFieldLabel =
          fieldName && !looksLikeRawProvenance(fieldName)
            ? FIELD_LABELS[fieldName]
            : undefined;

        position = mappedFieldLabel ? `${mappedFieldLabel} 항목` : "";
      }

      const unitRaw =
        item.provenance?.unit_declaration ||
        item.provenance?.unit_source ||
        "";

      const unit = looksLikeRawProvenance(unitRaw) ? "" : unitRaw;

      // 제출일은 evidence.doc_id가 source_documents와 exact join된 경우에만 값을
      // 얻을 수 있다(join 실패 시 sourceDoc 자체가 undefined). filing_date가 있어도
      // 형식이 맞지 않거나 실재하지 않는 날짜면 formatFilingDate가 null을 돌려주고,
      // 이때는 행 자체를 그린다.
      const sourceDoc = item.doc_id
        ? sourceDocumentIndex?.get(item.doc_id)
        : undefined;

      const filingDate = formatFilingDate(sourceDoc?.filing_date);

      return `
        <article class="evidence-item">
          <div class="document-icon" aria-hidden="true"></div>

          <div class="evidence-content">
            <p class="document-name">
              ${escapeHtml(resolveDocumentLabel(item, context, sourceDocumentIndex))}
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
                      <dt>근거 항목</dt>
                      <dd>${escapeHtml(position)}</dd>
                    </div>
                  `
                  : ""
              }

              ${
                filingDate
                  ? `
                    <div>
                      <dt>제출일</dt>
                      <dd>${escapeHtml(filingDate)}</dd>
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
    insertKoreanWonBreakdown(cleanAnswerText(response.answer)) ||
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
  renderEvidence(
    response.evidence,
    buildEvidenceContext(response),
    buildSourceDocumentIndex(response),
  );

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
      title: "조회 기준을 선택해 주세요",
      description:
        payload.error?.message ||
        "질문을 조금 더 구체적으로 입력해 주세요.",
      mode: "blocked",
      // 백엔드가 기계가 읽을 수 있는 후보 목록을 내려줄 때만 pill 버튼으로 보여준다.
      suggestions: payload.error?.details?.allowed_report_types,
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


// 답변 문장 안에서 "758,900,000,000원"처럼 숫자 바로 뒤에 원화 단위가 붙은 정수를
// 조/억/만 단위로 풀어 쓴다. formatMoneyKorean(조/억까지만, fact 카드 보조 표기용)과
// 달리 이 함수는 답변 문장 자체에 끼워 넣는 값이라 만 단위, 그리고 만 단위로도 나눠
// 떨어지지 않는 원 단위 잔액까지 그대로 남긴다 — 반올림·절사를 하지 않는다.
//
// 인자는 이미 "환산된 원 금액"(BigInt)이어야 한다 — 천원/백만원 등 원문 단위 배수는
// 호출부(insertKoreanWonBreakdown)에서 곱해서 넘긴다. 1만원 미만은 더 굵은 단위로
// 묶을 게 없어 null을 돌려주고, 호출부는 이때 원문 매치를 그대로 둔다("100원" 유지).
const koreanWonBreakdown = (wonAmount) => {
  const isNegative = wonAmount < 0n;
  const absValue = isNegative ? -wonAmount : wonAmount;

  if (absValue < 10000n) {
    return null;
  }

  const trillion = absValue / 1_000_000_000_000n;
  const afterTrillion = absValue % 1_000_000_000_000n;
  const eok = afterTrillion / 100_000_000n;
  const afterEok = afterTrillion % 100_000_000n;
  const man = afterEok / 10_000n;
  const won = afterEok % 10_000n;

  const parts = [];

  if (trillion > 0n) {
    parts.push(`${trillion.toLocaleString("ko-KR")}조`);
  }

  if (eok > 0n) {
    parts.push(`${eok.toLocaleString("ko-KR")}억`);
  }

  if (man > 0n) {
    parts.push(`${man.toLocaleString("ko-KR")}만`);
  }

  // 만 단위로도 나눠 떨어지지 않는 원 미만 잔액은 버리지 않고 "N원" 조각으로 남긴다
  // (예: "1,234만 5,000원"). 부호는 함수 끝에서 한 번만 붙인다.
  if (won > 0n) {
    parts.push(`${won.toLocaleString("ko-KR")}원`);
  }

  if (!parts.length) {
    return null;
  }

  const joined = parts.join(" ");
  const sign = isNegative ? "-" : "";

  // 마지막 조각이 이미 "원"으로 끝나면(원 미만 잔액이 있던 경우) 그대로 두고, 조/억/만
  // 단위까지만 있으면 문장에서 읽히도록 "원"을 한 번 덧붙인다.
  return parts[parts.length - 1].endsWith("원")
    ? sign + joined
    : `${sign}${joined} 원`;
};


// 숫자 바로 뒤에 원화 단위가 붙은 자리만 잡는다. 지원 단위는 "원"/"천원"/"천 원"/
// "백만원"/"백만 원"이며, 숫자-단위 사이와 "백만"/"천"-"원" 사이의 공백은 선택적이다.
// 연도("2025년")·비율("12.34%")·주식 수("1,237,964,708주")는 접미사가 달라 애초에
// 매칭되지 않는다. 앞에 "."/숫자/","가 오면(소수점의 뒷부분이거나 더 긴 숫자의
// 일부이면) 시작점으로 잡지 않아, 숫자를 조각내 잘못 변환하지 않는다. "17조
// 5,806억원"·"285억 3,352만 원"처럼 이미 조/억/만 단위가 붙은 표현은 숫자와 이
// 패턴의 단위 사이에 "조"/"억"/"만" 글자가 끼어 있어 애초에 맞지 않으므로 다시
// 변환되지 않는다. 단위 대체(alternation)는 "백만원" → "천원" → "원" 순으로 긴
// 단위부터 판정한다 — 순서를 반대로 두면 "백만원"의 마지막 "원"만 별도 단위로
// 잘못 매칭될 위험이 있다.
const WON_AMOUNT_PATTERN =
  /(?<![.\d,])(\d{1,3}(?:,\d{3})+|\d+) ?(백만 ?원|천 ?원|원)/g;

// 각 원문 단위를 정확히 원 단위로 환산하는 배수. 내부 공백을 지운 단위 텍스트
// ("백만 원" → "백만원")를 키로 쓴다.
const WON_UNIT_MULTIPLIERS = {
  원: 1n,
  천원: 1000n,
  백만원: 1000000n,
};


// 숫자를 JavaScript Number로 곱하면 333,605,938백만원처럼 큰 금액에서 정밀도를
// 잃을 수 있으므로, 쉼표를 뗀 정수 문자열을 BigInt로 바꾸고 단위 배수도 BigInt로
// 곱한다. 음수 부호("-758,900,000,000원")는 정규식이 애초에 캡처하지 않으므로
// 매치 앞에 그대로 남아 있다가 결과 문자열 앞에 자연스럽게 이어붙는다.
const insertKoreanWonBreakdown = (text) => {
  if (typeof text !== "string" || !text) {
    return text;
  }

  return text.replace(WON_AMOUNT_PATTERN, (match, digits, unitRaw) => {
    const multiplier = WON_UNIT_MULTIPLIERS[unitRaw.replace(/ /g, "")];

    if (multiplier === undefined) {
      return match;
    }

    const wonAmount = BigInt(digits.replace(/,/g, "")) * multiplier;
    const breakdown = koreanWonBreakdown(wonAmount);

    return breakdown || match;
  });
};