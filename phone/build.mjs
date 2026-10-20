// Builds dist/reelnote.js: the Scriptable script with ui.html inlined. Run: node phone/build.mjs
import { readFileSync, writeFileSync, mkdirSync } from "node:fs"
const dir = new URL(".", import.meta.url)
const src = readFileSync(new URL("reelnote.js", dir), "utf8")
const ui = readFileSync(new URL("ui.html", dir), "utf8")
if (!src.includes('"__UI_HTML__"') || !ui.includes("/*__INIT__*/")) throw new Error("placeholder missing")
mkdirSync(new URL("dist/", dir), { recursive: true })
writeFileSync(new URL("dist/reelnote.js", dir), src.replace('"__UI_HTML__"', () => JSON.stringify(ui)))
console.log("wrote phone/dist/reelnote.js")
