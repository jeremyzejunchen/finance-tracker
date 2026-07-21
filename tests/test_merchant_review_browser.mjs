import assert from "node:assert/strict";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { join, resolve } from "node:path";
import { spawn, spawnSync } from "node:child_process";

const repoRoot = resolve(import.meta.dirname, "..");
const tempRoot = join(repoRoot, ".tmp", "merchant-review-browser");
const databasePath = join(tempRoot, "merchant-review.sqlite3");
const startupPath = join(tempRoot, "start_server.py");
const playwrightWrapperPath = join(tempRoot, "playwright.ps1");
const playwrightCliPath = join(repoRoot, "node_modules", ".bin", "playwright-cli.cmd");
const session = `merchant-review-${process.pid}`;

function command(args) {
  const result = spawnSync("pwsh", ["-NoProfile", "-File", playwrightWrapperPath, ...args], {
    cwd: repoRoot, encoding: "utf8", timeout: 90000,
    env: { ...process.env, MERCHANT_REVIEW_PLAYWRIGHT_SESSION: session },
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
  mkdirSync(tempRoot, { recursive: true });
  writeFileSync(playwrightWrapperPath, `
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
& '${playwrightCliPath.replaceAll("'", "''")}' --session $env:MERCHANT_REVIEW_PLAYWRIGHT_SESSION @Arguments
exit $LASTEXITCODE
`, "utf8");

  const port = await freePort();
  writeFileSync(startupPath, `
from datetime import date
from decimal import Decimal
from pathlib import Path
from finance_tracker.app import build_server
from finance_tracker.db import Database
from finance_tracker.domain import ParsedTransaction
from finance_tracker.services import FinanceService

database_path = Path(r"${databasePath.replaceAll("\\", "\\\\")}")
database = Database(database_path)
database.initialize()
service = FinanceService(database)
rows = []
for index, amount in enumerate((Decimal("-10.00"), Decimal("-20.00"))):
    transaction = ParsedTransaction(
        booking_date=date(2026, 6, 1), value_date=date(2026, 6, 1), amount=amount,
        currency="EUR", merchant_raw="SYNTHETIC SHOP", merchant_normalized="SYNTHETIC SHOP",
        description_raw="Synthetic merchant review transaction", account="ME", source_format="synthetic",
        source_record_index=index, source_record_key=f"merchant-review:{index}", raw={"synthetic": True},
    )
    rows.append(service._prepare(transaction, "synthetic"))
database.write_import({"path": "", "filename": "merchant-review.csv", "source_type": "synthetic", "sha256": "merchant-review-browser"}, rows)
server = build_server("127.0.0.1", ${port}, database_path)
print("READY", flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
`, "utf8");

  const python = join(repoRoot, ".venv-phase1", "Scripts", "python.exe");
  assert.ok(existsSync(python), ".venv-phase1 interpreter is required");
  const server = spawn(python, [startupPath], {
    cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
  });
  try {
    await waitForReady(server);
    command(["open", `http://127.0.0.1:${port}/merchant-review`]);
    let page = snapshot();
    assert.match(page, /SYNTHETIC SHOP/);
    command(["click", refFor(page, "展开仅此笔")]);
    waitForRender();
    page = snapshot();
    assert.match(page, /逐笔覆盖/);
    console.log("Merchant review browser test: passed");
  } finally {
    spawnSync("pwsh", ["-NoProfile", "-File", playwrightWrapperPath, "close"], {
      cwd: repoRoot, timeout: 90000,
      env: { ...process.env, MERCHANT_REVIEW_PLAYWRIGHT_SESSION: session },
    });
    server.kill();
    rmSync(tempRoot, { recursive: true, force: true });
  }
}

await main();
