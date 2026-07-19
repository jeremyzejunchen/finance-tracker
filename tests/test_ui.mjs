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
const duplicateFindings = [{ code: "DUPLICATE_SOURCE_FILE", details: { filenames: ["a.pdf", "b.pdf"] } }];
assert.deepEqual(Array.from(ui.normalizedFilenameGroup("b.pdf,    a.pdf")), ["a.pdf", "b.pdf"]);
assert.equal(ui.legacyBlockerCoveredByAudit({ filename: "a.pdf, b.pdf" }, duplicateFindings), true);
assert.equal(ui.legacyBlockerCoveredByAudit({ filename: "a.pdf" }, duplicateFindings), false);
assert.equal(ui.legacyBlockerCoveredByAudit({ filename: "a.pdf, b.pdf" }, [{ code: "DUPLICATE_SOURCE_FILE", details: { filenames: ["c.pdf", "d.pdf"] } }]), false);
assert.equal(ui.legacyBlockerCoveredByAudit({ filename: "a.pdf" }, [{ code: "DUPLICATE_SOURCE_FILE", details: { filenames: ["a.pdf"] } }]), true);
const multipleDuplicateFindings = [
  ...duplicateFindings,
  { code: "DUPLICATE_SOURCE_FILE", details: { filenames: ["c.pdf", "d.pdf"] } },
];
const displayedBlockers = ui.legacyBlockersForDisplay({ audit: { findings: multipleDuplicateFindings }, blockers: [
  { filename: "b.pdf, a.pdf", error: "duplicate" },
  { filename: "d.pdf, c.pdf", error: "duplicate" },
  { filename: "a.pdf", error: "partial" },
  { filename: "other.pdf", error: "unrelated" },
] });
assert.deepEqual(Array.from(displayedBlockers).map(item => item.filename), ["a.pdf", "other.pdf"]);
assert.equal(ui.legacyBlockersForDisplay({ blockers: [{ filename: "a.pdf, b.pdf", error: "legacy" }] }).length, 1);
assert.equal(ui.legacyBlockersForDisplay({ audit: { findings: [{ code: "DUPLICATE_EXTERNAL_ID", details: {} }] }, blockers: [{ filename: "", error: "批次内存在重复 external ID" }] }).length, 0);

assert.equal(ui.merchantReviewPriority({ merchant: "Unknown bank transaction", category_reason: "unclassified" }), 0);
assert.equal(ui.merchantReviewPriority({ merchant: "SHOP", category_reason: "rule_conflict_contains" }), 1);
assert.equal(ui.merchantReviewAction({ key: "Enter" }), "apply");
assert.equal(ui.merchantReviewAction({ key: "s" }), "skip");
assert.equal(ui.merchantReviewAction({ key: "E" }), "override");
assert.equal(ui.merchantReviewAction({ key: "Enter", target: { tagName: "BUTTON" } }), "");
assert.equal(ui.merchantReviewAction({ key: "s", target: { tagName: "SELECT" } }), "");
const overrideState = { overrideCategoryIds: { 7: "4" }, selectedCategoryId: "2" };
assert.equal(ui.merchantReviewOverrideCategory(overrideState, { id: 7, category_id: 1 }), "4");
assert.equal(ui.merchantReviewOverrideCategory({ ...overrideState, error: "请求失败" }, { id: 7, category_id: 1 }), "4");

console.log("UI logic tests: 28 assertions passed");
