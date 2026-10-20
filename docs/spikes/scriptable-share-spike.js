// reelnote spike (throwaway): answers spec §8 spike 1 before plan B is written.
// 1. Does a Scriptable WebView open from Instagram's share sheet?
// 2. Does a JS -> Scriptable result handoff work there?
// 3. Can the share extension read and write reelnote-data with a Keychain token?
// 4. Is the full prototype smooth inside the share extension (memory limit)?
// Run once inside the Scriptable app (stores the token), then from Instagram: Share > Scriptable.

const REPO = "ChrisChae00/reelnote-data"
const PROTO = "https://raw.githubusercontent.com/ChrisChae00/reelnote/main/docs/prototype/share-sheet.html"
const KEY = "reelnote.github"
const log = []
const t0 = Date.now()
const note = (k, v) => log.push(`${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)

note("in share sheet", config.runsInActionExtension)
note("args.urls", args.urls)
note("args.plainTexts", args.plainTexts)

if (!Keychain.contains(KEY) && !config.runsInActionExtension) {
  const a = new Alert()
  a.title = "GitHub token"
  a.message = "Fine-grained PAT: reelnote-data only, Contents read/write."
  a.addSecureTextField("github_pat_...")
  a.addAction("Save")
  a.addCancelAction("Cancel")
  if ((await a.present()) === 0) Keychain.set(KEY, a.textFieldValue(0).trim())
}
note("token in Keychain", Keychain.contains(KEY))

async function gh(method, path, body) {
  const r = new Request(`https://api.github.com/repos/${REPO}/contents/${path}`)
  r.method = method
  r.headers = {
    Authorization: `Bearer ${Keychain.get(KEY)}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  }
  if (body) r.body = JSON.stringify(body)
  const json = await r.loadJSON()
  return { status: r.response.statusCode, json }
}

let cats = []
if (Keychain.contains(KEY)) {
  try {
    let t = Date.now()
    const got = await gh("GET", "vault/categories.md")
    note("GET categories.md", `${got.status} in ${Date.now() - t} ms`)
    if (got.status === 200) {
      cats = Data.fromBase64String(got.json.content.replace(/\n/g, "")).toRawString()
        .split("\n").map(s => s.trim()).filter(s => s && !s.startsWith("#"))
    }
    const path = `spike/ping-${Date.now()}.txt` // outside inbox/, so the Mac pipeline ignores it
    t = Date.now()
    const put = await gh("PUT", path, { message: "spike: write test", content: Data.fromString("ping").toBase64String() })
    note("PUT", `${put.status} in ${Date.now() - t} ms`)
    if (put.status === 201) note("DELETE", (await gh("DELETE", path, { message: "spike: clean up", sha: put.json.content.sha })).status)
  } catch (e) {
    note("GitHub error", String(e))
  }
}
note("categories (Korean must read correctly)", cats)

const shared = (args.urls || [])[0] || (args.plainTexts || [])[0] || ""
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c])
const html = `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font:17px -apple-system,system-ui;margin:0;padding:16px;background:#f2f2f7;color:#000}
@media(prefers-color-scheme:dark){body{background:#000;color:#fff}button{background:#1c1c1e;color:#fff}}
h1{font-size:28px;margin:8px 0 12px}button{display:block;width:100%;padding:14px;margin:6px 0;border:0;border-radius:12px;font:inherit;text-align:left;background:#fff}
input{width:100%;box-sizing:border-box;padding:12px;font:inherit;border-radius:10px;border:1px solid #ccc}
pre{white-space:pre-wrap;font-size:13px}
</style>
<h1>Spike</h1>
<p>Shared: ${esc(shared) || "(nothing)"}</p>
<input id="memo" placeholder="Memo (type Korean to test the keyboard)">
${["auto", ...cats].map(c => `<button data-c="${esc(c)}">${esc(c)}</button>`).join("")}
<pre id="log"></pre>
<script>
document.querySelectorAll("button").forEach(b => b.onclick = () =>
  window.done && window.done({ category: b.dataset.c, memo: document.getElementById("memo").value }))
function show(t){ document.querySelectorAll("button,input").forEach(e => e.remove()); document.getElementById("log").textContent = t + "\\n\\nClose this sheet to continue." }
</script>`

const wv = new WebView()
await wv.loadHTML(html)
const closed = wv.present(true).then(() => "closed without choosing")
const picked = await Promise.race([wv.evaluateJavaScript("window.done = completion; null", true), closed])
note("result from JS", picked)
note("total ms", Date.now() - t0)
Pasteboard.copy(log.join("\n"))
await wv.evaluateJavaScript(`show(${JSON.stringify(log.join("\n") + "\n\n(copied to clipboard)")})`).catch(() => {})
await closed

const a = new Alert()
a.title = "Open the full prototype?"
a.message = "Check that springs, sheet drag and edge swipe stay smooth here."
a.addAction("Open")
a.addCancelAction("Skip")
if ((await a.present()) === 0) {
  const proto = new WebView()
  await proto.loadHTML(await new Request(PROTO).loadString())
  await proto.present(true)
}
Script.complete()
