import assert from "node:assert/strict";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { join, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";

const repoRoot = resolve(import.meta.dirname, "..");
const tempRoot = join(repoRoot, ".tmp", "kontoumsaetze-browser");
const statementsRoot = join(tempRoot, "statements");
const databasePath = join(tempRoot, "kontoumsaetze.sqlite3");
const startupPath = join(tempRoot, "start_server.py");
const kontoumsaetzePath = join(statementsRoot, "Kontoumsaetze_synthetic-czj.csv");
const wifePath = join(statementsRoot, "synthetic-cr.csv");
const pdfPath = join(statementsRoot, "synthetic.pdf");
const playwrightWrapperPath = join(tempRoot, "playwright.ps1");
const session = `kontoumsaetze-${process.pid}`;

function command(args) {
  const result = spawnSync("pwsh", ["-NoProfile", "-File", playwrightWrapperPath, ...args], {
    cwd: repoRoot,
    encoding: "utf8",
    timeout: 90000,
    env: { ...process.env, KONTOUMSAETZE_PLAYWRIGHT_SESSION: session },
  });
  assert.equal(result.status, 0, `${args.join(" ")} failed:\n${result.error?.message || result.stderr || result.stdout}`);
  return result.stdout;
}

function snapshot() {
  return command(["snapshot"]);
}

function refFor(snapshotText, label) {
  const line = snapshotText.split(/\r?\n/).find(item => item.includes(label) && /\[ref=[^\]]+\]/.test(item));
  const match = line?.match(/\[ref=([^\]]+)\]/);
  assert.ok(match, `No Playwright element reference for ${label}:\n${snapshotText}`);
  return match[1];
}

function waitForRender() {
  command(["run-code", "await page.waitForTimeout(250)"]);
}

function freePort() {
  return new Promise((resolvePort, reject) => {
    const probe = createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(error => error ? reject(error) : resolvePort(port));
    });
  });
}

function waitForReady(process) {
  return new Promise((resolveReady, reject) => {
    let output = "";
    const timeout = setTimeout(() => reject(new Error(`Server did not start:\n${output}`)), 10000);
    process.stdout.on("data", chunk => {
      output += chunk;
      if (output.includes("READY")) {
        clearTimeout(timeout);
        resolveReady();
      }
    });
    process.stderr.on("data", chunk => { output += chunk; });
    process.once("exit", code => {
      clearTimeout(timeout);
      reject(new Error(`Server exited with ${code}:\n${output}`));
    });
  });
}

async function main() {
  rmSync(tempRoot, { recursive: true, force: true });
  mkdirSync(statementsRoot, { recursive: true });
  writeFileSync(kontoumsaetzePath, "\ufeffSynthetic metadata\nSynthetic metadata\nSynthetic metadata\nSynthetic metadata\nSynthetic metadata\nSynthetic metadata\nSynthetic metadata\nBuchungstag;Wert;Buchungstext;Begünstigter / Auftraggeber;Verwendungszweck;Betrag;Währung\n01.06.2026;01.06.2026;SEPA-Lastschrift;SYNTHETIC MARKET;Synthetic purchase;-12,34;EUR\n", "utf8");
  writeFileSync(wifePath, "date,merchant,amount\n2026-06-02,SYNTHETIC WIFE,-5.00\n", "utf8");
  writeFileSync(pdfPath, "%PDF-synthetic", "utf8");
  writeFileSync(playwrightWrapperPath, `
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
& npx --yes --package '@playwright/cli' playwright-cli --session $env:KONTOUMSAETZE_PLAYWRIGHT_SESSION @Arguments
exit $LASTEXITCODE
`, "utf8");

  const port = await freePort();
  writeFileSync(startupPath, `
from pathlib import Path
from finance_tracker.app import build_server

server = build_server("127.0.0.1", ${port}, Path(r"${databasePath.replaceAll("\\", "\\\\")}"), Path(r"${statementsRoot.replaceAll("\\", "\\\\")}"))
print("READY", flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
`, "utf8");

  const python = join(repoRoot, ".venv-phase1", "Scripts", "python.exe");
  assert.ok(existsSync(python), ".venv-phase1 interpreter is required");
  const server = spawn(python, [startupPath], { cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" } });
  try {
    await waitForReady(server);
    command(["open", `http://127.0.0.1:${port}/import`]);
    let page = snapshot();
    command(["click", refFor(page, "扫描银行流水目录")]);
    waitForRender();
    page = snapshot();
    assert.match(page, /Kontoumsaetze_synthetic-czj\.csv/);
    assert.match(page, /synthetic-cr\.csv/);
    assert.match(page, /ME/);
    assert.match(page, /WIFE/);
    assert.doesNotMatch(page, /synthetic\.pdf/);

    command(["click", refFor(page, "账单文件")]);
    command(["upload", kontoumsaetzePath]);
    page = snapshot();
    command(["click", refFor(page, "解析并预览")]);
    waitForRender();
    page = snapshot();
    assert.match(page, /kontoumsaetze_csv/);
    assert.match(page, /SYNTHETIC MARKET/);
    assert.match(page, /ME/);
    command(["check", refFor(page, "我已经检查本次导入数据")]);
    page = snapshot();
    command(["click", refFor(page, "统一确认导入")]);
    waitForRender();
    page = snapshot();
    assert.match(page, /写入 1 笔/);

    command(["reload"]);
    waitForRender();
    page = snapshot();
    command(["click", refFor(page, "账单文件")]);
    command(["upload", pdfPath]);
    command(["click", refFor(page, "解析并预览")]);
    waitForRender();
    page = snapshot();
    assert.match(page, /文件解析失败/);
    assert.match(page, /统一确认导入" \[disabled\]/);
    console.log("Kontoumsaetze browser test: passed");
  } finally {
    spawnSync("pwsh", ["-NoProfile", "-File", playwrightWrapperPath, "close"], { cwd: repoRoot, timeout: 90000, env: { ...process.env, KONTOUMSAETZE_PLAYWRIGHT_SESSION: session } });
    server.kill();
    rmSync(tempRoot, { recursive: true, force: true });
  }
}

await main();
