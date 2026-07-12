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
    <section class="panel"><h2>支出分类</h2>${summary.categories.length ? summary.categories.map(x => `<div class="chart-row"><span>${x.name}</span><div class="bar" style="width:${(x.amount / max) * 100}%"></div><span>${money(x.amount)}</span></div>`).join("") : '<p class="label">暂无已统计的欧元交易。</p>'}</section>
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
        <label>账单文件<input name="statements" type="file" accept=".pdf,.csv" multiple required></label>
        <label>来源路径（可选，仅用于审计）<input name="source_path" placeholder="例如 D:\\Statements"></label>
        <button>解析并预览</button>
      </form>
    </section>
    <section id="preview" class="panel hidden"></section>
  </div>`;
  document.querySelector("#import-form").onsubmit = async event => {
    event.preventDefault();
    const form = new FormData(event.target);
    const target = document.querySelector("#preview");
    try {
      const data = await request("/api/import/preview", { method: "POST", body: form });
      target.classList.remove("hidden");
      const baseline = data.baseline?.available
        ? `<p class="${data.baseline.different ? "warning" : "notice"}">基准对比：${data.baseline.different ? "存在差异" : "一致"}</p>`
        : "";
      target.innerHTML = `<h2>批量导入预览</h2>${baseline}${data.previews.map(preview => `<article><h3>${preview.filename}</h3>${preview.duplicate_source ? '<p class="warning">该文件已导入，不会重复写入。</p>' : ""}<p>${preview.source_type}，识别 ${preview.total} 笔，欧元 ${preview.eur_transactions}，非欧元 ${preview.unsupported_currency}</p>${table(["日期", "商户", "金额", "币种"], preview.sample.map(item => `<tr><td>${item.booking_date}</td><td>${item.merchant_normalized || item.merchant}</td><td>${item.amount}</td><td>${item.currency}</td></tr>`))}</article>`).join("")}${data.blockers.map(item => `<p class="warning">${item.filename}：${item.error}</p>`).join("")}${data.can_confirm ? '<button id="confirm">统一确认导入</button>' : '<p class="warning">请先移除或解决异常文件。</p>'}`;
      if (data.can_confirm) {
        document.querySelector("#confirm").onclick = async () => {
          const source = form.get("source_path");
          const result = await request("/api/import/confirm", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              items: data.previews.map(item => ({
                token: item.token,
                source_path: source ? `${source}\\${item.filename}` : "",
              })),
            }),
          });
          target.insertAdjacentHTML("beforeend", result.results.map((item, index) => item.ok ? `<p class="notice">${data.previews[index].filename}：写入 ${item.inserted || 0} 笔${item.duplicate_source ? "（重复来源，已跳过）" : ""}。</p>` : `<p class="warning">${data.previews[index].filename}：${item.error}</p>`).join(""));
          document.querySelector("#confirm").disabled = true;
        };
      }
    } catch (error) {
      target.classList.remove("hidden");
      target.innerHTML = `<p class="warning">${error.message}</p>`;
    }
  };
}

async function transactions(review = false) {
  const [rows, categories] = await Promise.all([request("/api/transactions"), request("/api/categories")]);
  const filtered = review ? rows.filter(x => x.category_status === "unclassified" || x.excluded_reason || x.unsupported_currency) : rows;
  const options = categories.map(c => `<option value="${c.id}">${c.level1} / ${c.level2} / ${c.level3}</option>`).join("");
  app.innerHTML = `<section class="panel">
    <p class="label">${review ? "待复核包含未分类、排除统计、非欧元和自动对账建议。" : "人工修改分类会保留审计记录，重新导入不会覆盖。"}</p>
    <label>搜索商户或说明<input id="tx-search" placeholder="输入关键词"></label>
    <div id="tx-table">${table(["日期", "商户", "金额", "来源", "分类", "状态"], filtered.map(row => `<tr data-search="${(row.merchant + " " + row.description).toLowerCase()}"><td>${row.booking_date}</td><td title="${row.description}">${row.merchant}</td><td class="${row.amount_cents >= 0 ? "amount-positive" : "amount-negative"}">${money(row.amount_cents)}</td><td>${row.filename}</td><td><select data-id="${row.id}">${options}</select></td><td>${row.unsupported_currency ? "非欧元" : row.excluded_reason || row.category_status}</td></tr>`))}</div>
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
    <section class="panel">${table(["一级", "二级", "三级", "口径"], rows.map(row => `<tr><td>${row.level1}</td><td>${row.level2}</td><td>${row.level3}</td><td>${row.bucket}</td></tr>`))}</section>
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

const page = document.body.dataset.page;
({ "/": report, "/import": importPage, "/transactions": () => transactions(false), "/review": () => transactions(true), "/categories": categories }[page])()
  .catch(error => {
    app.innerHTML = `<p class="warning">${error.message}</p>`;
  });
