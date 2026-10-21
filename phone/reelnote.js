// reelnote share sheet for Scriptable. Source: phone/reelnote.js + phone/ui.html.
// Install the built file phone/dist/reelnote.js (node phone/build.mjs), not this one.
// Scriptable owns the GitHub token; the WebView only sends requests over the reelnote:// bridge.

const REPO = "ChrisChae00/reelnote-data"
const KEY = "reelnote.github"
const CATS = "vault/categories.md"
const COUNTS = "vault/.reelnote/category-counts.json"
const TAG_COUNTS = "vault/.reelnote/tag-counts.json"
const CATS_HEADER = "# Categories, most recently created or used first. One per line.\n"
const UI_HTML = "__UI_HTML__" // build.mjs replaces this with phone/ui.html

// ---------- categories.md, edited line by line so comments and hand edits survive ----------
const isName = (l) => l.trim() !== "" && !l.trim().startsWith("#")
const toLines = (text) => (text.trim() === "" ? [] : text.replace(/\n$/, "").split("\n"))
const fromLines = (lines) => lines.join("\n") + "\n"

function names(text) {
  const out = []
  for (const l of toLines(text)) if (isName(l) && !out.includes(l.trim())) out.push(l.trim())
  return out
}
function moveToTop(text, name) {
  const lines = toLines(text).filter((l) => l.trim() !== name)
  let i = 0
  while (i < lines.length && !isName(lines[i])) i++
  lines.splice(i, 0, name)
  return fromLines(lines)
}
function removeName(text, name) {
  return fromLines(toLines(text).filter((l) => l.trim() !== name))
}
function renameName(text, from, to) { // same rule as the Mac's categories.apply_rename
  const had = names(text)
  if (!had.includes(from)) return text
  const out = []
  for (const l of toLines(text)) {
    if (l.trim() !== from) out.push(l)
    else if (!had.includes(to)) out.push(to)
  }
  return fromLines(out)
}
function checkName(name) {
  if (typeof name !== "string" || name !== name.trim() || !name) throw new Error("카테고리 이름이 비어 있어요.")
  if (name.length > 30 || /[\r\n]/.test(name)) throw new Error("카테고리 이름은 한 줄, 30자 이내여야 해요.")
  if (name.startsWith("#") || name.toLowerCase() === "auto") throw new Error(`'${name}'은(는) 카테고리 이름으로 쓸 수 없어요.`)
  return name
}

// same rule as the Mac's vault.tag_name: Obsidian tags have no spaces and are not all digits
function tagName(t) {
  if (typeof t !== "string") return null
  const n = t.trim().replace(/^#+/, "").replace(/\s+/g, "_").replace(/[^\p{L}\p{M}\p{N}_\/-]/gu, "").slice(0, 40)
  return n && !/^\d+$/.test(n) ? n : null
}
function cleanTags(tags) {
  if (tags == null) return []
  if (!Array.isArray(tags)) throw new Error("태그 형식이 잘못됐어요.")
  const out = []
  for (const t of tags) { const n = tagName(t); if (n && !out.includes(n)) out.push(n) }
  if (out.length > 10) throw new Error("태그는 10개까지 붙일 수 있어요.")
  return out
}

// ---------- inbox files: inbox/<YYYYMMDD-HHMMSS>-<4 hex>.json ----------
const pad = (n) => String(n).padStart(2, "0")
function isoLocal(d) {
  const off = -d.getTimezoneOffset(), a = Math.abs(off)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
    `${off >= 0 ? "+" : "-"}${pad(Math.floor(a / 60))}:${pad(a % 60)}`
}
function inboxPath(d, hex) {
  return `inbox/${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}-${hex}.json`
}
const randomHex = () => Math.floor(Math.random() * 0x10000).toString(16).padStart(4, "0")
const firstUrl = (s) => ((typeof s === "string" && s.match(/https?:\/\/\S+/)) || [""])[0]

// ---------- GitHub Contents API ----------
async function gh(method, path, body) {
  const r = new Request(`https://api.github.com/repos/${REPO}/contents/${path}`)
  r.method = method
  r.headers = {
    Authorization: `Bearer ${Keychain.get(KEY)}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  }
  if (body) r.body = JSON.stringify(body)
  let json
  try {
    json = await r.loadJSON()
  } catch (e) {
    throw new Error("GitHub에 연결하지 못했어요. 인터넷 연결을 확인하고 다시 시도해주세요.")
  }
  const status = r.response.statusCode
  if (status === 401) {
    Keychain.remove(KEY) // the next run asks for a new token
    throw new Error("GitHub 토큰이 만료됐거나 잘못됐어요. 공유를 다시 열면 새 토큰을 넣을 수 있어요.")
  }
  if (status === 403 || (status === 404 && method !== "GET")) {
    throw new Error(`GitHub가 거절했어요(${status}). 토큰에 reelnote-data의 Contents 읽기/쓰기 권한이 있는지 확인해주세요.`)
  }
  return { status, json }
}
const decode = (b64) => Data.fromBase64String(b64.replace(/\n/g, "")).toRawString()
const encode = (text) => Data.fromString(text).toBase64String()

let catsCache = null // { text, sha }; sha null while the file does not exist yet
async function readCats() {
  const { status, json } = await gh("GET", CATS)
  if (status === 404) return { text: CATS_HEADER, sha: null }
  if (status !== 200) throw new Error(`categories.md를 읽지 못했어요(${status}).`)
  return { text: decode(json.content), sha: json.sha }
}
async function readCounts(path) {
  const { status, json } = await gh("GET", path)
  if (status !== 200) return {}
  try { return JSON.parse(decode(json.content)) } catch (e) { return {} }
}
// the Mac also rewrites categories.md, so a stale sha (409/422) means: read again and redo the edit
async function editCats(edit, message) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const cur = catsCache || (catsCache = await readCats())
    const next = edit(cur.text)
    if (next === cur.text) return
    const body = { message, content: encode(next) }
    if (cur.sha) body.sha = cur.sha
    const { status, json } = await gh("PUT", CATS, body)
    if (status === 200 || status === 201) { catsCache = { text: next, sha: json.content.sha }; return }
    catsCache = null
    if (status !== 409 && status !== 422) throw new Error(`categories.md를 저장하지 못했어요(${status}).`)
  }
  throw new Error("Mac이 동시에 카테고리를 바꾸는 중이에요. 잠시 뒤 다시 시도해주세요.")
}
async function putInbox(item, message) {
  const now = new Date()
  const { status } = await gh("PUT", inboxPath(now, randomHex()), {
    message, content: encode(JSON.stringify({ ...item, created_at: isoLocal(now) }, null, 2) + "\n"),
  })
  if (status !== 201) throw new Error(`링크를 저장하지 못했어요(${status}).`)
}

// ---------- requests from the page ----------
async function handle(msg) {
  if (msg.op === "save") {
    const category = msg.category === "auto" ? "auto" : checkName(msg.category)
    if (typeof msg.video_url !== "string" || !/^https?:\/\/(www\.)?instagram\.com\//.test(msg.video_url)) throw new Error("영상 링크가 인스타그램 주소가 아니에요.")
    const opt = (v) => (typeof v === "string" && v.trim() ? v.trim() : null)
    const tags = cleanTags(msg.tags)
    // categories.md first: the Mac turns a category it cannot find into auto
    if (category !== "auto") await editCats((t) => moveToTop(t, category), `categories: use ${category}`)
    await putInbox({ op: "save", video_url: msg.video_url.trim(), dm_url: opt(msg.dm_url), memo: opt(msg.memo), category, tags }, "inbox: save")
  } else if (msg.op === "create") {
    const name = checkName(msg.name)
    await editCats((t) => moveToTop(t, name), `categories: create ${name}`)
  } else if (msg.op === "rename") {
    const from = checkName(msg.from), to = checkName(msg.to)
    // rename op first: if the Mac runs in between, it renames notes instead of treating the old name as deleted
    await putInbox({ op: "rename_category", from, to }, `inbox: rename ${from} -> ${to}`)
    await editCats((t) => renameName(t, from, to), `categories: rename ${from} -> ${to}`)
  } else if (msg.op === "delete") {
    const name = checkName(msg.name)
    await editCats((t) => removeName(t, name), `categories: delete ${name}`)
  } else {
    throw new Error(`알 수 없는 요청: ${msg.op}`)
  }
  return { ok: true }
}

async function askToken() {
  const a = new Alert()
  a.title = "GitHub 토큰"
  a.message = "reelnote-data 저장소 하나에 Contents 읽기/쓰기 권한만 준 fine-grained 토큰을 붙여넣어 주세요. 이 폰의 Keychain에만 저장돼요."
  a.addSecureTextField("github_pat_…")
  a.addAction("저장")
  a.addCancelAction("취소")
  if ((await a.present()) !== 0 || !a.textFieldValue(0).trim()) return false
  Keychain.set(KEY, a.textFieldValue(0).trim())
  return true
}
async function fail(message) {
  const a = new Alert()
  a.title = "reelnote"
  a.message = message
  a.addAction("확인")
  await a.present()
}

async function main() {
  if (!Keychain.contains(KEY) && !(await askToken())) return
  let counts, tagCounts
  try {
    [catsCache, counts, tagCounts] = await Promise.all([readCats(), readCounts(COUNTS), readCounts(TAG_COUNTS)])
  } catch (e) {
    return fail(e.message)
  }
  const shared = [...(args.urls || []), ...(args.plainTexts || [])].map(firstUrl).find(Boolean) || ""
  const init = {
    cats: names(catsCache.text).map((name) => ({ name, count: counts[name] || 0 })),
    tags: Object.entries(tagCounts).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count),
    shared, inShareSheet: config.runsInActionExtension,
  }
  // "<" escaped so a category name cannot close the script tag; a function replacement so "$&" stays literal
  const html = UI_HTML.replace("/*__INIT__*/", () => `window.REELNOTE_INIT = ${JSON.stringify(init).replace(/</g, "\\u003c")};`)
  const fm = FileManager.local()
  const file = fm.joinPath(fm.temporaryDirectory(), "reelnote.html")
  fm.writeString(file, html)

  const wv = new WebView()
  let line = Promise.resolve()
  const reply = (id, res) => wv.evaluateJavaScript(`bridgeReply(${JSON.stringify(id)}, ${JSON.stringify(res)})`).catch(() => {})
  wv.shouldAllowRequest = (req) => {
    const url = req.url || ""
    if (!url.startsWith("reelnote:")) return url.startsWith("file:") || url === "about:blank" // the page never leaves itself
    let msg
    try { msg = JSON.parse(decodeURIComponent(url.slice(url.indexOf("?") + 1))) } catch (e) { return false }
    if (msg.op === "close") { Script.complete(); return false }
    line = line.then(() => handle(msg)).then((res) => reply(msg.id, res), (e) => reply(msg.id, { ok: false, error: e.message }))
    return false
  }
  await wv.loadFile(file)
  await wv.present(true)
}

if (typeof Script === "undefined") {
  module.exports = { names, moveToTop, removeName, renameName, checkName, tagName, cleanTags, isoLocal, inboxPath, firstUrl, handle, editCats, setCache: (c) => { catsCache = c } }
} else {
  await main()
  Script.complete()
}
