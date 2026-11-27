import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
const mode = process.argv[2] === "start" ? "start" : "dev";
if (existsSync(".env.local")) process.loadEnvFile(".env.local");
if (!existsSync(".venv/bin/python")) {
  console.error("Python environment missing. Follow README.md setup, then run npm run dev.");
  process.exit(1);
}
const children = [
  spawn(".venv/bin/python", ["-m", "uvicorn", "worker.main:app", "--host", "127.0.0.1", "--port", "8000"], { stdio: "inherit" }),
  spawn(process.execPath, ["node_modules/next/dist/bin/next", mode, ...(mode === "dev" ? ["--webpack"] : []), "--hostname", process.env.CUTLIST_WEB_HOST || "127.0.0.1", "--port", "3000"], { stdio: "inherit", env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" } }),
];
let closing = false;
function stop(code = 0) {
  if (closing) return;
  closing = true;
  process.exitCode = code;
  for (const child of children) child.kill("SIGTERM");
  setTimeout(() => { for (const child of children) child.kill("SIGKILL"); process.exit(code); }, 2500).unref();
}
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => stop());
for (const child of children) {
  child.on("error", error => { console.error(error.message); stop(1); });
  child.on("exit", code => { if (!closing) stop(code ?? 1); });
}
