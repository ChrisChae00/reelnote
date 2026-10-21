// Tests the Scriptable side against fake Scriptable globals. Run: node phone/test.mjs
import { readFileSync } from "node:fs"
import assert from "node:assert/strict"

const src = readFileSync(new URL("reelnote.js", import.meta.url), "utf8")
const repo = new Map() // path -> { text, sha }
const calls = []
let shaN = 0, conflictOnce = false
class Request {
  constructor(url) { this.url = url; this.method = "GET" }
  async loadJSON() {
    const path = this.url.split("/contents/")[1], body = this.body ? JSON.parse(this.body) : null
    calls.push(`${this.method} ${path}`)
    const cur = repo.get(path)
    if (this.method === "GET") { this.response = { statusCode: cur ? 200 : 404 }; return cur ? { content: Buffer.from(cur.text).toString("base64"), sha: cur.sha } : {} }
    if (this.method === "PUT") {
      if (conflictOnce && path.endsWith("categories.md")) { // the Mac committed in between
        conflictOnce = false
        repo.set(path, { text: cur.text + "Mac\n", sha: `s${++shaN}` })
        this.response = { statusCode: 409 }; return {}
      }
      if ((cur && body.sha !== cur.sha) || (!cur && body.sha)) { this.response = { statusCode: 409 }; return {} }
      const next = { text: Buffer.from(body.content, "base64").toString("utf8"), sha: `s${++shaN}` }
      repo.set(path, next); this.response = { statusCode: cur ? 200 : 201 }; return { content: { sha: next.sha } }
    }
  }
}
const Keychain = { get: () => "t", remove() {} }
const Data = {
  fromString: (s) => ({ toBase64String: () => Buffer.from(s, "utf8").toString("base64") }),
  fromBase64String: (b) => ({ toRawString: () => Buffer.from(b, "base64").toString("utf8") }),
}
const mod = { exports: {} }
await new (Object.getPrototypeOf(async () => {}).constructor)("module", "Request", "Keychain", "Data", src)(mod, Request, Keychain, Data)
const m = mod.exports

// categories.md line edits
const H = "# Categories, most recently created or used first. One per line.\n"
assert.equal(m.moveToTop(H + "개발\n요리\n", "요리"), H + "요리\n개발\n")
assert.equal(m.moveToTop(H + "개발\n", "AI"), H + "AI\n개발\n")
assert.equal(m.moveToTop("", "AI"), "AI\n")
assert.equal(m.moveToTop(H + "개발\n# keep me\n요리\n", "요리"), H + "요리\n개발\n# keep me\n")
assert.equal(m.removeName(H + "개발\n요리\n", "개발"), H + "요리\n")
assert.equal(m.renameName(H + "개발\n요리\n", "개발", "Dev"), H + "Dev\n요리\n")
assert.equal(m.renameName(H + "개발\n요리\n", "개발", "요리"), H + "요리\n") // merge into an existing name
assert.equal(m.renameName(H + "요리\n", "개발", "Dev"), H + "요리\n")
assert.deepEqual(m.names(H + "개발\n 요리 \n개발\n\n"), ["개발", "요리"])
for (const bad of ["", " a", "#x", "auto", "a\nb", "x".repeat(31)]) assert.throws(() => m.checkName(bad))

// inbox naming matches the Mac's parser: created_at is ISO 8601 with offset
const d = new Date(2026, 9, 20, 9, 5, 7)
assert.equal(m.inboxPath(d, "0a1b"), "inbox/20261020-090507-0a1b.json")
assert.match(m.isoLocal(d), /^2026-10-20T09:05:07[+-]\d\d:\d\d$/)
assert.equal(m.firstUrl("see https://www.instagram.com/reel/X/ now"), "https://www.instagram.com/reel/X/")

// save with a manual category: categories.md is updated before the inbox file
repo.set("vault/categories.md", { text: H + "개발\n요리\n", sha: "s0" })
assert.deepEqual(await m.handle({ op: "save", category: "요리", video_url: "https://www.instagram.com/reel/ABC/", dm_url: " ", memo: "메모" }), { ok: true })
assert.equal(repo.get("vault/categories.md").text, H + "요리\n개발\n")
const inbox = [...repo.keys()].filter((p) => p.startsWith("inbox/"))
assert.equal(inbox.length, 1)
const item = JSON.parse(repo.get(inbox[0]).text)
assert.deepEqual({ ...item, created_at: undefined }, { op: "save", video_url: "https://www.instagram.com/reel/ABC/", dm_url: null, memo: "메모", category: "요리", tags: [], created_at: undefined })
assert.ok(calls.indexOf("PUT vault/categories.md") < calls.findIndex((c) => c.startsWith("PUT inbox/")))

// auto does not touch categories.md
calls.length = 0
await m.handle({ op: "save", category: "auto", video_url: "https://www.instagram.com/p/XYZ/" })
assert.ok(!calls.includes("PUT vault/categories.md"))

// a stale sha (the Mac committed meanwhile) re-reads and redoes the edit without losing the Mac's line
conflictOnce = true
await m.handle({ op: "create", name: "AI" })
assert.equal(repo.get("vault/categories.md").text, H + "AI\n요리\n개발\nMac\n")

// rename writes the rename op before categories.md
calls.length = 0
await m.handle({ op: "rename", from: "AI", to: "인공지능" })
assert.ok(calls.findIndex((c) => c.startsWith("PUT inbox/")) < calls.indexOf("PUT vault/categories.md"))
assert.equal(repo.get("vault/categories.md").text, H + "인공지능\n요리\n개발\nMac\n")

await m.handle({ op: "delete", name: "Mac" })
assert.equal(repo.get("vault/categories.md").text, H + "인공지능\n요리\n개발\n")
await assert.rejects(m.handle({ op: "save", category: "auto", video_url: "https://evil.example/" }))
await assert.rejects(m.handle({ op: "rm" }))
await assert.rejects(m.handle({ op: "save", category: "auto", video_url: "https://www.instagram.com/p/XYZ/", tags: "AI" }))

// tags: same normalization as the Mac (vault.tag_name)
assert.equal(m.tagName("Job Hunting"), "Job_Hunting")
assert.equal(m.tagName(" #AI "), "AI")
assert.equal(m.tagName("면접/코딩"), "면접/코딩")
assert.equal(m.tagName("2026"), null)
assert.deepEqual(m.cleanTags(["AI", "#AI", "Job Hunting", "!!"]), ["AI", "Job_Hunting"])
assert.throws(() => m.cleanTags(Array.from({ length: 11 }, (_, i) => `t${i}`)))
console.log("phone tests passed")
