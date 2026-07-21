const app = document.querySelector("#app");

const money = cents =>
  new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format((cents || 0) / 100);

const request = async (url, options = {}) => {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
};

const table = (headers, rows) =>
  `<div class="table-wrap"><table><thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

const auditSeverityOrder = ["blocker", "warning", "info"];
const auditFindingPresentation = {
  DUPLICATE_SOURCE_FILE: ["重复的账单文件", "这个文件以前已经导入过，或者本次选择了内容完全相同的文件。"],
  DUPLICATE_EXTERNAL_ID: ["重复的交易编号", "同一来源中有多条交易使用了相同的外部交易编号。"],
  UNSUPPORTED_CURRENCY: ["包含不支持的币种", "当前统一导入只允许 EUR 交易。"],
  IMPORT_ERROR: ["文件解析失败", "文件无法解析，请检查文件格式后重试。"],
  NO_TRANSACTIONS: ["未识别到交易", "文件中没有识别到可导入的交易。"],
  TRANSACTION_WARNING: ["交易需要注意", "这笔交易包含需要核对的解析提示。"],
  PARSER_WARNING: ["文件解析警告", "文件解析时发现需要核对的提示。"],
};

function auditStatusPresentation(status) {
  return {
    pass: ["检查通过", "未发现阻止导入的问题。", "good"],
    warning: ["需要注意", "发现需要核对的问题，但仍可确认导入。", "warning"],
    blocked: ["无法导入", "发现阻止导入的问题，处理后才能继续。", "bad"],
  }[status] || ["检查通过", "未发现阻止导入的问题。", "good"];
}

function confirmationState(data, checked) {
  const canConfirm = data.audit ? data.audit.can_confirm : data.can_confirm;
  return { blocked: !canConfirm, checkboxDisabled: !canConfirm, buttonDisabled: !canConfirm || !checked };
}

function groupedAuditFindings(audit) {
  return auditSeverityOrder.map(severity => ({ severity, findings: (audit.findings || []).filter(item => item.severity === severity) })).filter(group => group.findings.length);
}

function presentationForFinding(finding) {
  return auditFindingPresentation[finding.code] || ["其他审计提示", "发现一条需要核对的审计提示。"];
}

function auditRows(state) {
  return state.data.transactions.map((row, index) => ({ ...row, audit_index: index }));
}

function normalizedFilenameGroup(value) {
  const names = Array.isArray(value) ? value : String(value ?? "").split(",");
  return names.map(name => String(name).trim()).filter(Boolean).sort();
}

function legacyBlockerCoveredByAudit(blocker, auditFindings) {
  const blockerGroup = normalizedFilenameGroup(blocker.filename);
  if (!blockerGroup.length) return false;
  return auditFindings.some(finding => {
    if (finding.code !== "DUPLICATE_SOURCE_FILE") return false;
    const auditGroup = normalizedFilenameGroup(finding.details?.filenames);
    return auditGroup.length === blockerGroup.length && auditGroup.every((name, index) => name === blockerGroup[index]);
  });
}

function legacyBlockersForDisplay(data) {
  if (!data.audit) return data.blockers || [];
  return (data.blockers || []).filter(item => {
    if (legacyBlockerCoveredByAudit(item, data.audit.findings || [])) return false;
    const findings = data.audit.findings || [];
    return !findings.some(finding => {
      const details = finding.details || {};
      if (finding.code === "DUPLICATE_EXTERNAL_ID") return item.error?.includes("external ID");
      if (finding.code === "UNSUPPORTED_CURRENCY") return (data.previews || []).some(preview => preview.filename === item.filename && preview.unsupported_currency);
      if (finding.code === "IMPORT_ERROR" || finding.code === "NO_TRANSACTIONS") return details.filename === item.filename && finding.message === item.error;
      return false;
    });
  });
}

function statusForPreviewRow(row) {
  if (row.duplicate_source) return "重复来源";
  if ((row.currency || "").toUpperCase() !== "EUR") return "非 EUR";
  if (row.transaction_kind === "currency_exchange") return "换汇";
  if (row.is_failed_transaction) return "失败交易";
  if (row.is_internal_transfer) return "内部转账";
  if (row.warnings?.length) return "Warning";
  if (!row.merchant_normalized || row.merchant_normalized.toLowerCase().startsWith("unknown ")) return "未识别商户";
  return "正常";
}

function transactionStatusLabel(row) {
  if (row.unsupported_currency) return "非 EUR";
  if (row.excluded_reason === "currency_exchange") return "换汇";
  if (row.excluded_reason === "internal_transfer") return "内部转账";
  if (row.excluded_reason === "failed_transaction") return "失败交易";
  return row.excluded_reason || row.category_status;
}

function buildPreviewState(data) {
  return {
    data,
    auditIndexes: null,
    sourceFile: "",
    account: "",
    sourceType: "",
    status: "all",
    quick: "all",
    search: "",
    sortKey: "booking_date",
    sortDirection: "desc",
    renderedCount: 0,
    chunkSize: 120,
  };
}

function filterPreviewTransactions(state) {
  let rows = auditRows(state);
  if (state.auditIndexes) rows = rows.filter(row => state.auditIndexes.has(row.audit_index));
  if (state.sourceFile) rows = rows.filter(row => row.filename === state.sourceFile);
  if (state.account) rows = rows.filter(row => row.account === state.account);
  if (state.sourceType) rows = rows.filter(row => row.source_type === state.sourceType);
  if (state.search) {
    const needle = state.search.toLowerCase();
    rows = rows.filter(row =>
      (row.merchant_raw || "").toLowerCase().includes(needle) ||
      (row.merchant_normalized || "").toLowerCase().includes(needle) ||
      (row.description_raw || "").toLowerCase().includes(needle),
    );
  }
  if (state.status !== "all") rows = rows.filter(row => statusForPreviewRow(row) === state.status);
  if (state.quick === "warnings") rows = rows.filter(row => row.warnings?.length);
  if (state.quick === "internal") rows = rows.filter(row => row.is_internal_transfer);
  if (state.quick === "exchange") rows = rows.filter(row => row.transaction_kind === "currency_exchange");
  if (state.quick === "failed") rows = rows.filter(row => row.is_failed_transaction);
  if (state.quick === "non_eur") rows = rows.filter(row => (row.currency || "").toUpperCase() !== "EUR");
  if (state.quick === "unknown") rows = rows.filter(row => !row.merchant_normalized || row.merchant_normalized.toLowerCase().startsWith("unknown "));
  rows.sort((left, right) => {
    const direction = state.sortDirection === "asc" ? 1 : -1;
    const a = state.sortKey === "amount" ? Number(left.amount) : left[state.sortKey] ?? "";
    const b = state.sortKey === "amount" ? Number(right.amount) : right[state.sortKey] ?? "";
    if (a < b) return -1 * direction;
    if (a > b) return 1 * direction;
    return 0;
  });
  return rows;
}

function renderAuditFinding(finding, onFilter) {
  const [title, fallback] = presentationForFinding(finding);
  const body = auditFindingPresentation[finding.code] ? fallback : (finding.message || fallback);
  const details = finding.details || {};
  const detailParts = [];
  if (finding.code === "DUPLICATE_SOURCE_FILE") detailParts.push(`文件：${(details.filenames || []).join("、")}；出现 ${details.occurrence_count || 0} 次${details.exists_in_database ? "；数据库中已有相同文件" : ""}`);
  if (finding.code === "DUPLICATE_EXTERNAL_ID") detailParts.push(`来源：${details.source_type || "—"}；交易编号：${details.external_id || "—"}；出现 ${details.occurrence_count || 0} 次`);
  if (finding.code === "TRANSACTION_WARNING" || finding.code === "PARSER_WARNING") detailParts.push(details.warning || finding.message || fallback);
  const indexes = Array.isArray(finding.transaction_indexes) ? finding.transaction_indexes : [];
  return `<article class="audit-finding audit-${escapeHtml(finding.severity)}"><h4>${escapeHtml(title)}</h4><p>${escapeHtml(body)}</p>${detailParts.length ? `<p class="label">${escapeHtml(detailParts.join(" "))}</p>` : ""}<p class="audit-code">代码：${escapeHtml(finding.code)}</p>${indexes.length ? `<button type="button" class="secondary audit-filter-button" data-audit-indexes="${escapeHtml(JSON.stringify(indexes))}">查看相关交易</button>` : ""}</article>`;
}

function renderAuditPanel(audit) {
  if (!audit) return "";
  const [statusTitle, statusText, statusClass] = auditStatusPresentation(audit.status);
  const metrics = [["源文件", audit.source_file_count], ["解析交易", audit.parsed_transaction_count], ["排除交易", audit.excluded_transaction_count], ["阻止问题", audit.blocking_finding_count], ["警告问题", audit.warning_finding_count], ["提示信息", audit.info_finding_count], ["含警告交易", audit.warning_transaction_count]];
  const totals = Object.entries(audit.totals_by_currency || {}).map(([currency, value]) => `<span class="audit-total"><b>${escapeHtml(currency)}</b> ${escapeHtml(value)}</span>`).join("") || "—";
  const groups = groupedAuditFindings(audit).map(group => `<section class="audit-group"><h3>${group.severity === "blocker" ? "阻止问题" : group.severity === "warning" ? "警告" : "信息"}</h3>${group.findings.map(renderAuditFinding).join("")}</section>`).join("");
  return `<section class="panel audit-panel"><div class="audit-status audit-${statusClass}" role="status"><span aria-hidden="true">${audit.status === "blocked" ? "⛔" : audit.status === "warning" ? "⚠" : "✓"}</span><div><h2>${escapeHtml(statusTitle)}</h2><p>${escapeHtml(statusText)}</p></div></div><div class="audit-metrics">${metrics.map(([label, value]) => `<div class="card"><div class="label">${escapeHtml(label)}</div><div class="metric">${escapeHtml(value)}</div></div>`).join("")}</div><p><strong>按币种总额：</strong>${totals}</p>${groups || '<p class="notice">本次检查没有需要显示的审计发现。</p>'}<div id="audit-filter-notice" class="notice hidden"></div></section>`;
}

function renderPreviewRows(rows, count) {
  return rows.slice(0, count).map(row => {
    const description = escapeHtml(row.description_raw);
    const warningText = row.warnings?.length ? `<div class="label">${row.warnings.map(escapeHtml).join("<br>")}</div>` : "";
    return `<tr>
      <td>${escapeHtml(row.filename)}</td>
      <td>${escapeHtml(row.account)}</td>
      <td>${row.booking_date}</td>
      <td>${row.value_date}</td>
      <td>${escapeHtml(row.merchant_raw)}</td>
      <td>${escapeHtml(row.merchant_normalized)}</td>
      <td><details><summary>${escapeHtml((row.description_raw || "").slice(0, 60) || "—")}</summary><div>${description || "—"}${warningText}</div></details></td>
      <td class="${Number(row.amount) >= 0 ? "amount-positive" : "amount-negative"}">${escapeHtml(row.amount)}</td>
      <td>${escapeHtml(row.currency)}</td>
      <td>${escapeHtml(row.transaction_type)}</td>
      <td>${statusForPreviewRow(row)}</td>
    </tr>`;
  }).join("");
}

function renderImportPreview(target, state) {
  const rows = filterPreviewTransactions(state);
  const fileOptions = [...new Set(state.data.transactions.map(item => item.filename))].sort();
  const accountOptions = [...new Set(state.data.transactions.map(item => item.account))].sort();
  const sourceOptions = [...new Set(state.data.transactions.map(item => item.source_type))].sort();
  const stats = state.data.stats;
  const normalCount = stats.total - stats.warning_count - stats.internal_transfer_count - stats.currency_exchange_count - stats.failed_transaction_count - stats.unsupported_currency_count;
  state.renderedCount = Math.min(rows.length, state.renderedCount || state.chunkSize);
  const canShowMore = state.renderedCount < rows.length;
  const baseline = state.data.baseline?.available
    ? `<p class="${state.data.baseline.different ? "warning" : "notice"}">基准对比：${state.data.baseline.different ? "存在差异" : "一致"}</p>`
    : "";
  target.innerHTML = `<div class="stack">
    <section class="panel">
      <div class="grid">
        <div class="card"><div class="label">总交易数</div><div class="metric">${stats.total}</div></div>
        <div class="card"><div class="label">正常数量</div><div class="metric">${Math.max(0, normalCount)}</div></div>
        <div class="card"><div class="label">Warning 数量</div><div class="metric">${stats.warning_count}</div></div>
        <div class="card"><div class="label">内部转账</div><div class="metric">${stats.internal_transfer_count}</div></div>
        <div class="card"><div class="label">换汇</div><div class="metric">${stats.currency_exchange_count}</div></div>
        <div class="card"><div class="label">失败交易</div><div class="metric">${stats.failed_transaction_count}</div></div>
        <div class="card"><div class="label">非 EUR</div><div class="metric">${stats.unsupported_currency_count}</div></div>
      </div>
      ${baseline}
      ${legacyBlockersForDisplay(state.data).map(item => `<p class="warning">${escapeHtml(item.filename)}：${escapeHtml(item.error)}</p>`).join("")}
    </section>
    ${renderAuditPanel(state.data.audit)}
    <section class="panel">
      <h2>文件汇总</h2>
      ${table(
        ["文件名", "来源类型", "识别笔数", "日期范围", "收入", "支出", "Warning", "重复文件"],
        state.data.previews.map(item => `<tr><td>${escapeHtml(item.filename)}</td><td>${escapeHtml(item.source_type)}</td><td>${item.total}</td><td>${item.date_from || "—"} ~ ${item.date_to || "—"}</td><td>${money(item.income_cents)}</td><td>${money(item.expense_cents)}</td><td>${item.warning_count}</td><td>${item.duplicate_source ? "是" : "否"}</td></tr>`),
      )}
    </section>
    <section class="panel">
      <h2>全部交易预览</h2>
      <div class="form-row">
        <label>搜索<input id="preview-search" value="${escapeHtml(state.search)}" placeholder="搜索商户和说明"></label>
        <label>来源文件<select id="preview-file"><option value="">全部</option>${fileOptions.map(item => `<option value="${escapeHtml(item)}"${item === state.sourceFile ? " selected" : ""}>${escapeHtml(item)}</option>`).join("")}</select></label>
        <label>账户<select id="preview-account"><option value="">全部</option>${accountOptions.map(item => `<option value="${escapeHtml(item)}"${item === state.account ? " selected" : ""}>${escapeHtml(item)}</option>`).join("")}</select></label>
        <label>source_type<select id="preview-source-type"><option value="">全部</option>${sourceOptions.map(item => `<option value="${escapeHtml(item)}"${item === state.sourceType ? " selected" : ""}>${escapeHtml(item)}</option>`).join("")}</select></label>
        <label>状态<select id="preview-status"><option value="all">全部</option>${["正常", "Warning", "内部转账", "换汇", "失败交易", "非 EUR", "重复来源", "未识别商户"].map(item => `<option value="${item}"${item === state.status ? " selected" : ""}>${item}</option>`).join("")}</select></label>
        <label>排序<select id="preview-sort"><option value="booking_date:desc"${state.sortKey === "booking_date" && state.sortDirection === "desc" ? " selected" : ""}>日期 ↓</option><option value="booking_date:asc"${state.sortKey === "booking_date" && state.sortDirection === "asc" ? " selected" : ""}>日期 ↑</option><option value="amount:desc"${state.sortKey === "amount" && state.sortDirection === "desc" ? " selected" : ""}>金额 ↓</option><option value="amount:asc"${state.sortKey === "amount" && state.sortDirection === "asc" ? " selected" : ""}>金额 ↑</option></select></label>
      </div>
      <div class="form-row">
        <button type="button" class="${state.quick === "all" ? "" : "secondary"}" data-quick="all">全部</button>
        <button type="button" class="${state.quick === "warnings" ? "" : "secondary"}" data-quick="warnings">仅 Warning</button>
        <button type="button" class="${state.quick === "internal" ? "" : "secondary"}" data-quick="internal">内部转账</button>
        <button type="button" class="${state.quick === "exchange" ? "" : "secondary"}" data-quick="exchange">换汇</button>
        <button type="button" class="${state.quick === "failed" ? "" : "secondary"}" data-quick="failed">失败交易</button>
        <button type="button" class="${state.quick === "non_eur" ? "" : "secondary"}" data-quick="non_eur">非 EUR</button>
        <button type="button" class="${state.quick === "unknown" ? "" : "secondary"}" data-quick="unknown">未识别商户</button>
      </div>
      <p class="label">当前显示 ${Math.min(state.renderedCount, rows.length)} / ${rows.length}，本次总交易 ${stats.total}</p>
      ${table(["来源文件", "账户", "Booking date", "Value date", "原始商户", "清洗后商户", "说明", "金额", "币种", "交易类型", "状态"], [renderPreviewRows(rows, state.renderedCount)])}
      ${canShowMore ? '<button type="button" id="preview-more" class="secondary">继续显示</button>' : ""}
    </section>
    <section class="panel">
      <label><input id="confirm-check" type="checkbox"> ${state.data.audit?.status === "warning" ? "我已查看以上警告，并确认继续导入" : "我已经检查本次导入数据"}</label>
      <div class="form-row">
        <button type="button" id="confirm" disabled>统一确认导入</button>
      </div>
      ${state.data.audit && !state.data.audit.can_confirm ? '<p class="warning">必须先处理上面的阻止问题，才能确认导入。</p>' : state.data.audit ? "" : state.data.can_confirm ? "" : '<p class="warning">存在解析失败、非 EUR 阻断条件或零交易文件，暂不能确认导入。</p>'}
    </section>
  </div>`;

  document.querySelector("#preview-search").oninput = event => {
    state.search = event.target.value;
    state.renderedCount = state.chunkSize;
    renderImportPreview(target, state);
  };
  document.querySelector("#preview-file").onchange = event => {
    state.sourceFile = event.target.value;
    state.renderedCount = state.chunkSize;
    renderImportPreview(target, state);
  };
  document.querySelector("#preview-account").onchange = event => {
    state.account = event.target.value;
    state.renderedCount = state.chunkSize;
    renderImportPreview(target, state);
  };
  document.querySelector("#preview-source-type").onchange = event => {
    state.sourceType = event.target.value;
    state.renderedCount = state.chunkSize;
    renderImportPreview(target, state);
  };
  document.querySelector("#preview-status").onchange = event => {
    state.status = event.target.value;
    state.renderedCount = state.chunkSize;
    renderImportPreview(target, state);
  };
  document.querySelector("#preview-sort").onchange = event => {
    const [key, direction] = event.target.value.split(":");
    state.sortKey = key;
    state.sortDirection = direction;
    renderImportPreview(target, state);
  };
  document.querySelectorAll("[data-quick]").forEach(button => {
    button.onclick = () => {
      state.quick = button.dataset.quick;
      state.renderedCount = state.chunkSize;
      renderImportPreview(target, state);
    };
  });
  const moreButton = document.querySelector("#preview-more");
  if (moreButton) {
    moreButton.onclick = () => {
      state.renderedCount += state.chunkSize;
      renderImportPreview(target, state);
    };
  }
  document.querySelectorAll(".audit-filter-button").forEach(button => {
    button.onclick = () => {
      state.auditIndexes = new Set(JSON.parse(button.dataset.auditIndexes));
      state.renderedCount = state.chunkSize;
      renderImportPreview(target, state);
      const notice = document.querySelector("#audit-filter-notice");
      if (notice) {
        notice.classList.remove("hidden");
        notice.innerHTML = `当前仅显示审计发现相关交易。<button type="button" class="secondary" id="clear-audit-filter">清除审计筛选</button>`;
        document.querySelector("#clear-audit-filter").onclick = () => {
          state.auditIndexes = null;
          state.renderedCount = state.chunkSize;
          renderImportPreview(target, state);
        };
      }
    };
  });
}

async function report(query = "") {
  const [summary, transactions, categories] = await Promise.all([
    request("/api/report" + query),
    request("/api/transactions"),
    request("/api/categories"),
  ]);
  const accounts = [...new Set(transactions.map(x => x.account))];
  const sources = [...new Set(transactions.map(x => x.source_type))];
  const max = Math.max(...summary.categories.map(x => x.amount), 1);
  app.innerHTML = `<div class="stack">
    <section class="panel">
      <form id="report-filter" class="form-row">
        <label>开始日期<input name="date_from" type="date"></label>
        <label>结束日期<input name="date_to" type="date"></label>
        <label>账户<select name="account"><option value="">全部</option>${accounts.map(x => `<option>${x}</option>`).join("")}</select></label>
        <label>来源<select name="source"><option value="">全部</option>${sources.map(x => `<option value="${x}">${x}</option>`).join("")}</select></label>
        <label>分类<select name="category"><option value="">全部</option>${categories.map(x => `<option value="${x.id}">${x.level2} / ${x.level3}</option>`).join("")}</select></label>
        <button>筛选</button>
      </form>
    </section>
    <div class="grid">
      <div class="card"><div class="label">收入</div><div class="metric good">${money(summary.income)}</div></div>
      <div class="card"><div class="label">支出</div><div class="metric bad">${money(summary.expense)}</div></div>
      <div class="card"><div class="label">净额</div><div class="metric ${summary.net >= 0 ? "good" : "bad"}">${money(summary.net)}</div></div>
      <div class="card"><div class="label">有效交易</div><div class="metric">${summary.count}</div></div>
    </div>
    <section class="panel"><h2>支出分类</h2>${summary.categories.length ? summary.categories.map(x => `<div class="chart-row"><span>${x.name}</span><div class="bar" style="width:${(x.amount / max) * 100}%"></div><span>${money(x.amount)}</span></div>`).join("") : '<p class="label">暂无已统计的 EUR 交易。</p>'}</section>
    <section class="panel"><h2>月度收支</h2>${table(["月份", "收入", "支出", "净额"], summary.monthly.map(x => `<tr><td>${x.month}</td><td class="amount-positive">${money(x.income)}</td><td class="amount-negative">${money(-x.expense)}</td><td>${money(x.income - x.expense)}</td></tr>`))}</section>
  </div>`;
  document.querySelector("#report-filter").onsubmit = event => {
    event.preventDefault();
    const queryString = new URLSearchParams(Object.fromEntries([...new FormData(event.target)].filter(([, value]) => value !== "")));
    report("?" + queryString);
  };
}

async function importPage() {
  app.innerHTML = `<div class="stack">
    <section class="panel">
      <p class="notice">可一次选择多个账单；文件只在本次导入中读取，不会复制或修改原文件。</p>
      <form id="import-form" class="form-row">
        <label>账单文件<input name="statements" type="file" accept=".csv" multiple required></label>
        <label>来源路径（可选，仅用于审计）<input name="source_path" placeholder="例如 D:\\Statements"></label>
        <button>解析并预览</button>
      </form>
      <button id="scan-directory" type="button">扫描银行流水目录</button>
      <section id="scan-results" class="hidden"></section>
    </section>
    <section id="preview" class="panel hidden"></section>
  </div>`;
  const target = document.querySelector("#preview");
  const renderPreview = (data, sourcePaths = {}, source = "") => {
    target.classList.remove("hidden");
    const state = buildPreviewState(data);
    state.renderedCount = state.chunkSize;
    renderImportPreview(target, state);
    const checkbox = document.querySelector("#confirm-check");
    const button = document.querySelector("#confirm");
    if (!checkbox || !button) return;
    const updateConfirmation = () => {
      const current = confirmationState(data, checkbox.checked);
      checkbox.disabled = current.checkboxDisabled;
      button.disabled = current.buttonDisabled;
    };
    updateConfirmation();
    checkbox.onchange = updateConfirmation;
    button.onclick = async () => {
      const result = await request("/api/import/confirm", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: data.previews.map(item => ({ token: item.token, source_path: sourcePaths[item.token] || (source ? `${source}\\${item.filename}` : "") })) }),
      });
      target.insertAdjacentHTML("beforeend", `<section class="panel">${result.results.map((item, index) => item.ok ? `<p class="notice">${escapeHtml(data.previews[index].filename)}：写入 ${item.inserted || 0} 笔${item.duplicate_source ? "（重复来源，已跳过）" : ""}。</p>` : `<p class="warning">${escapeHtml(data.previews[index].filename)}：${escapeHtml(item.error)}</p>`).join("")}</section>`);
      button.disabled = true;
    };
  };
  document.querySelector("#import-form").onsubmit = async event => {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      const data = await request("/api/import/preview", { method: "POST", body: form });
      renderPreview(data, {}, form.get("source_path"));
    } catch (error) {
      target.classList.remove("hidden");
      target.innerHTML = `<p class="warning">${escapeHtml(error.message)}</p>`;
    }
  };
  document.querySelector("#scan-directory").onclick = async () => {
    const scan = await request("/api/import/scan");
    const holder = document.querySelector("#scan-results");
    holder.classList.remove("hidden");
    holder.innerHTML = `${scan.files.map(file => `<label><input type="checkbox" value="${escapeHtml(file.relative_path)}" ${file.status === "ready" ? "" : "disabled"}>${escapeHtml(file.relative_path)} · ${escapeHtml(file.account)} · ${escapeHtml(file.status)}</label>`).join("<br>")}<button id="preview-scanned" type="button">预览所选文件</button>`;
    document.querySelector("#preview-scanned").onclick = async () => {
      const relative_paths = [...holder.querySelectorAll("input:checked")].map(input => input.value);
      const data = await request("/api/import/preview-scanned", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ relative_paths }) });
      renderPreview(data, data.source_paths);
    };
  };
}

async function transactions(review = false) {
  const [rows, categories] = await Promise.all([request("/api/transactions"), request("/api/categories")]);
  const filtered = review ? rows.filter(x => x.category_status === "unclassified" || x.excluded_reason || x.unsupported_currency) : rows;
  const options = categories.map(c => `<option value="${c.id}">${c.level1} / ${c.level2} / ${c.level3}</option>`).join("");
  app.innerHTML = `<section class="panel">
    <p class="label">${review ? "待复核包含未分类、排除统计、非 EUR 和自动对账建议。" : "人工修改分类会保留审计记录，重新导入不会覆盖。"}</p>
    <label>搜索商户或说明<input id="tx-search" placeholder="输入关键词"></label>
    <div id="tx-table">${table(["日期", "商户", "金额", "来源", "分类", "状态"], filtered.map(row => `<tr data-search="${escapeHtml((row.merchant + " " + row.description).toLowerCase())}"><td>${row.booking_date}</td><td title="${escapeHtml(row.description)}">${escapeHtml(row.merchant)}</td><td class="${row.amount_cents >= 0 ? "amount-positive" : "amount-negative"}">${money(row.amount_cents)}</td><td>${escapeHtml(row.filename)}</td><td><select data-id="${row.id}">${options}</select></td><td>${escapeHtml(transactionStatusLabel(row))}</td></tr>`))}</div>
  </section>`;
  filtered.forEach(row => {
    const select = document.querySelector(`select[data-id="${row.id}"]`);
    if (select) {
      select.value = row.category_id;
      select.onchange = async () => {
        await request("/api/transactions/category", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ transaction_id: row.id, category_id: Number(select.value) }),
        });
      };
    }
  });
  document.querySelector("#tx-search").oninput = event => {
    document.querySelectorAll("#tx-table tbody tr").forEach(row => {
      row.hidden = !row.dataset.search.includes(event.target.value.toLowerCase());
    });
  };
}

async function categories() {
  const rows = await request("/api/categories");
  app.innerHTML = `<div class="stack">
    <section class="panel">
      <h2>添加分类</h2>
      <p class="notice">第一阶段只保留解析、清洗、去重和对账能力。规则学习 UI 留到第二阶段。</p>
      <form id="cat-form" class="form-row">
        <label>一级<input name="level1" required></label>
        <label>二级<input name="level2" required></label>
        <label>三级<input name="level3" required></label>
        <label>统计口径<select name="bucket"><option value="income">收入</option><option value="expense">支出</option><option value="excluded">排除</option><option value="investment">投资</option></select></label>
        <button>添加分类</button>
      </form>
    </section>
    <section class="panel">${table(["一级", "二级", "三级", "口径"], rows.map(row => `<tr><td>${escapeHtml(row.level1)}</td><td>${escapeHtml(row.level2)}</td><td>${escapeHtml(row.level3)}</td><td>${escapeHtml(row.bucket)}</td></tr>`))}</section>
  </div>`;
  document.querySelector("#cat-form").onsubmit = async event => {
    event.preventDefault();
    const value = Object.fromEntries(new FormData(event.target));
    await request("/api/categories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(value),
    });
    categories();
  };
}

if (typeof globalThis !== "undefined") {
  globalThis.financeTrackerUi = { auditStatusPresentation, confirmationState, groupedAuditFindings, presentationForFinding, renderAuditFinding, filterPreviewTransactions, normalizedFilenameGroup, legacyBlockerCoveredByAudit, legacyBlockersForDisplay };
}

const page = document.body.dataset.page;
({ "/": report, "/import": importPage, "/transactions": () => transactions(false), "/review": () => transactions(true), "/categories": categories }[page])()
  .catch(error => {
    app.innerHTML = `<p class="warning">${escapeHtml(error.message)}</p>`;
  });
