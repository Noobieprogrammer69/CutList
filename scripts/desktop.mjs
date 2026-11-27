// Portable launcher: installs project dependencies, starts the app, opens a window.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, openSync, closeSync, writeFileSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
process.chdir(root);
const data = path.join(root, "data"); mkdirSync(data, { recursive: true });
const logfile = path.join(data, "desktop.log");
if (existsSync(logfile) && statSync(logfile).size > 5 * 1024 * 1024) writeFileSync(logfile, "");
const log = openSync(logfile, "a");
const pidfile = path.join(data, "desktop.pid");
const env = { ...process.env, NEXT_TELEMETRY_DISABLED: "1", UV_CACHE_DIR: path.join(data, "package-cache") };
let application;
function notify(text) { const child = spawn("notify-send", ["Cutlist", text], { stdio: "ignore" }); child.on("error", () => {}); }
function run(command, args) {
  return new Promise((resolve, reject) => {
    const process = spawn(command, args, { cwd: root, env, stdio: ["ignore", log, log] });
    process.on("error", reject); process.on("exit", code => code === 0 ? resolve() : reject(new Error(`${command} failed (${code}). See ${logfile}`)));
  });
}
async function reachable() { try { const response = await fetch("http://127.0.0.1:3000/api/health", { signal: AbortSignal.timeout(1200) }); if (!response.ok) return false; const health = await response.json(); return health.api_revision >= 2 ? true : "outdated"; } catch { return false; } }
function open() { const child = spawn("xdg-open", ["http://127.0.0.1:3000"], { stdio: "ignore", detached: true }); child.on("error", () => notify("Open http://127.0.0.1:3000 in your browser.")); child.unref(); }
try {
  const existing = await reachable();
  if (existing === "outdated") throw new Error("An older Cutlist worker is running. Close its terminal/process, then open Cutlist again.");
  if (existing === true) { open(); closeSync(log); process.exit(0); }
  if (existsSync(pidfile)) {
    const pid = Number(readFileSync(pidfile, "utf8"));
    try { if (!Number.isInteger(pid) || pid < 2) throw new Error("stale launcher"); process.kill(pid, 0); notify("Cutlist is starting. Please wait for its browser window."); closeSync(log); process.exit(0); } catch {}
  }
  writeFileSync(pidfile, String(process.pid));
  notify("Starting Cutlist. Initial setup may download project dependencies; details are in data/desktop.log.");
  if (!existsSync(".venv/bin/python")) await run("uv", ["venv", "--python", "3.12", ".venv"]);
  await run("uv", ["pip", "install", "--python", ".venv/bin/python", "-r", "worker/requirements.txt"]);
  if (!existsSync("node_modules/next")) await run("npm", ["ci"]);
  // Rebuild after source changes; no build is required on ordinary launches.
  const { execFileSync } = await import("node:child_process");
  const fingerprint = execFileSync(process.execPath, ["scripts/source-stamp.mjs"], { encoding: "utf8" }).trim();
  const stamp = path.join(data, "desktop-build.json");
  if (!existsSync(".next/BUILD_ID") || !existsSync(stamp) || readFileSync(stamp, "utf8") !== fingerprint) {
    await run("npm", ["run", "build"]); writeFileSync(stamp, fingerprint);
  }
  application = spawn(process.execPath, ["scripts/run.mjs", "start"], { cwd: root, env, stdio: ["ignore", log, log] });
  application.on("error", error => notify(error.message));
  let ready = false;
  for (let i = 0; i < 90; i++) {
    if (await reachable() === true) { ready = true; open(); notify("Cutlist is ready. Open Setup in the app to install optional models."); break; }
    if (application.exitCode !== null) break;
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  if (!ready) throw new Error(`Could not start Cutlist. Inspect ${logfile}; check ports 3000/8000 and required packages.`);
  application.on("exit", () => { writeFileSync(pidfile, ""); closeSync(log); });
  for (const signal of ["SIGTERM", "SIGINT"]) process.on(signal, () => application.kill("SIGTERM"));
} catch (error) {
  application?.kill("SIGTERM");
  writeFileSync(pidfile, ""); notify(error.message); closeSync(log); process.exitCode = 1;
}
