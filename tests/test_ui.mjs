import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("finance_tracker/static/app.js", "utf8").split("const page = document.body.dataset.page;")[0];
const context = { document: { querySelector: () => ({}) }, globalThis: {} };
vm.createContext(context);
vm.runInContext(source, context);
const ui = context.globalThis.financeTrackerUi;

assert.deepEqual(Array.from(ui.auditStatusPresentation("pass")), ["检查通过", "未发现阻止导入的问题。", "good"]);
assert.deepEqual(Array.from(ui.auditStatusPresentation("warning")), ["需要注意", "发现需要核对的问题，但仍可确认导入。", "warning"]);
assert.deepEqual(Array.from(ui.auditStatusPresentation("blocked")), ["无法导入", "发现阻止导入的问题，处理后才能继续。", "bad"]);
assert.equal(ui.presentationForFinding({ code: "FUTURE_CODE" })[0], "其他审计提示");
assert.match(ui.renderAuditFinding({ code: "FUTURE_CODE", severity: "warning", message: "<script>" }), /&lt;script&gt;/);

const findings = [
  { severity: "info", code: "I" },
  { severity: "blocker", code: "B1" },
  { severity: "warning", code: "W" },
  { severity: "blocker", code: "B2" },
];
assert.deepEqual(Array.from(ui.groupedAuditFindings({ findings })).map(group => Array.from(group.findings).map(item => item.code)), [["B1", "B2"], ["W"], ["I"]]);

const data = { can_confirm: true, transactions: [{ filename: "a" }, { filename: "b" }, { filename: "a" }] };
const state = { data, auditIndexes: new Set([0, 2]), sourceFile: "", account: "", sourceType: "", status: "all", quick: "all", search: "", sortKey: "booking_date", sortDirection: "desc" };
const rows = ui.filterPreviewTransactions(state);
assert.deepEqual(Array.from(rows).map(row => row.audit_index).sort(), [0, 2]);
assert.equal(data.transactions[0].audit_index, undefined);
assert.equal(ui.confirmationState({ audit: { can_confirm: false }, can_confirm: true }, false).blocked, true);
assert.equal(ui.confirmationState({ audit: { can_confirm: false }, can_confirm: true }, false).checkboxDisabled, true);
assert.equal(ui.confirmationState({ audit: { can_confirm: true } }, false).buttonDisabled, true);
const duplicateFindings = [{ code: "DUPLICATE_SOURCE_FILE", details: { filenames: ["a.csv", "b.csv"] } }];
assert.deepEqual(Array.from(ui.normalizedFilenameGroup("b.csv,    a.csv")), ["a.csv", "b.csv"]);
assert.equal(ui.legacyBlockerCoveredByAudit({ filename: "a.csv, b.csv" }, duplicateFindings), true);
assert.equal(ui.legacyBlockerCoveredByAudit({ filename: "a.csv" }, duplicateFindings), false);
assert.equal(ui.legacyBlockerCoveredByAudit({ filename: "a.csv, b.csv" }, [{ code: "DUPLICATE_SOURCE_FILE", details: { filenames: ["c.csv", "d.csv"] } }]), false);
assert.equal(ui.legacyBlockerCoveredByAudit({ filename: "a.csv" }, [{ code: "DUPLICATE_SOURCE_FILE", details: { filenames: ["a.csv"] } }]), true);
const multipleDuplicateFindings = [
  ...duplicateFindings,
  { code: "DUPLICATE_SOURCE_FILE", details: { filenames: ["c.csv", "d.csv"] } },
];
const displayedBlockers = ui.legacyBlockersForDisplay({ audit: { findings: multipleDuplicateFindings }, blockers: [
  { filename: "b.csv, a.csv", error: "duplicate" },
  { filename: "d.csv, c.csv", error: "duplicate" },
  { filename: "a.csv", error: "partial" },
  { filename: "other.csv", error: "unrelated" },
] });
assert.deepEqual(Array.from(displayedBlockers).map(item => item.filename), ["a.csv", "other.csv"]);
assert.equal(ui.legacyBlockersForDisplay({ blockers: [{ filename: "a.csv, b.csv", error: "legacy" }] }).length, 1);
assert.equal(ui.legacyBlockersForDisplay({ audit: { findings: [{ code: "DUPLICATE_EXTERNAL_ID", details: {} }] }, blockers: [{ filename: "", error: "批次内存在重复 external ID" }] }).length, 0);

console.log("UI logic tests: 8 assertions passed");
