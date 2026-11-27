import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
const hash = createHash("sha256");
function walk(folder) { for (const file of readdirSync(folder, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) { const target = path.join(folder, file.name); if (file.isDirectory()) walk(target); else hash.update(target).update(readFileSync(target)); } }
walk("app"); hash.update(readFileSync("package-lock.json")).update(readFileSync("next.config.ts"));
console.log(hash.digest("hex"));
