import json
import subprocess
import pytest
from reelnote.llm import Chain, note_prompt, extract_json, BadOutput, AllProvidersLimited, NOTE_SCHEMA, CLASSIFY_SCHEMA
from reelnote.store import Store

NOTE = {"title": "맥북 생산성 앱 7개", "summary": "s", "key_facts": ["Raycast: 런처"], "keywords": ["mac"], "category": None}

def cp(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)

def ok(cmd, obj):
    # codex writes its answer to the -o file; the others print it
    if "-o" in cmd:
        open(cmd[cmd.index("-o") + 1], "w").write(json.dumps(obj))
        return cp()
    return cp(out=json.dumps(obj))

def test_extract_json_envelopes():
    assert extract_json(json.dumps(NOTE), NOTE_SCHEMA) == NOTE
    assert extract_json(json.dumps({"type": "result", "structured_output": NOTE}), NOTE_SCHEMA) == NOTE
    assert extract_json(json.dumps({"result": "```json\n" + json.dumps(NOTE) + "\n```"}), NOTE_SCHEMA) == NOTE

def test_extract_json_rejects_wrong_types():
    with pytest.raises(BadOutput):
        extract_json(json.dumps({**NOTE, "key_facts": "not a list"}), NOTE_SCHEMA)
    with pytest.raises(BadOutput):
        extract_json("I can't help with that", NOTE_SCHEMA)

def test_first_provider_success(tmp_path):
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd[0]); return cp(out=json.dumps({"structured_output": NOTE}))
    assert Chain(Store(tmp_path / "db"), ("claude", "codex"), 60, runner=runner).run("p", NOTE_SCHEMA) == NOTE
    assert calls == ["claude"]

def test_limit_falls_back_and_sets_cooldown(tmp_path):
    store = Store(tmp_path / "db")
    def runner(cmd, **kw):
        if cmd[0] == "claude":
            return cp(1, err="Claude usage limit reached. Your limit will reset at 5pm")
        return ok(cmd, {"category": "생산성"})
    out = Chain(store, ("claude", "codex"), 60, runner=runner, now=lambda: 1000.0).run("p", CLASSIFY_SCHEMA)
    assert out == {"category": "생산성"}
    assert store.cooldown_until("claude") == 1000.0 + 3600

def test_cooled_down_provider_skipped_until_reset(tmp_path):
    store = Store(tmp_path / "db"); store.set_cooldown("claude", 2000.0)
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd[0]); return ok(cmd, {"category": "a"})
    Chain(store, ("claude", "codex"), 60, runner=runner, now=lambda: 1500.0).run("p", CLASSIFY_SCHEMA)
    Chain(store, ("claude", "codex"), 60, runner=runner, now=lambda: 2500.0).run("p", CLASSIFY_SCHEMA)
    assert calls == ["codex", "claude"]

def test_all_limited(tmp_path):
    runner = lambda cmd, **kw: cp(1, err="429 Too Many Requests")
    with pytest.raises(AllProvidersLimited):
        Chain(Store(tmp_path / "db"), ("claude", "codex"), 60, runner=runner).run("p", CLASSIFY_SCHEMA)

def test_bad_output_retried_once_then_next_provider(tmp_path):
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd[0])
        return cp(out="not json") if cmd[0] == "claude" else cp(out=json.dumps({"category": "a"}))
    Chain(Store(tmp_path / "db"), ("claude", "codex"), 60, runner=runner).run("p", CLASSIFY_SCHEMA)
    assert calls == ["claude", "claude", "codex"]

def test_codex_reads_output_file(tmp_path):
    def runner(cmd, **kw):
        out = cmd[cmd.index("-o") + 1]
        open(out, "w").write(json.dumps({"category": "a"}))
        assert kw["cwd"] and "--output-schema" in cmd
        return cp(out="log noise")
    assert Chain(Store(tmp_path / "db"), ("codex",), 60, runner=runner).run("p", CLASSIFY_SCHEMA) == {"category": "a"}

def test_missing_cli_skipped(tmp_path):
    def runner(cmd, **kw):
        if cmd[0] == "claude":
            raise FileNotFoundError("claude")
        return ok(cmd, {"category": "a"})
    assert Chain(Store(tmp_path / "db"), ("claude", "codex"), 60, runner=runner).run("p", CLASSIFY_SCHEMA) == {"category": "a"}

def test_not_logged_in_skipped_without_cooldown(tmp_path):
    store = Store(tmp_path / "db")
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd[0])
        if cmd[0] == "claude":
            return cp(0, out=json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}))
        return ok(cmd, {"category": "a"})
    assert Chain(store, ("claude", "codex"), 60, runner=runner).run("p", CLASSIFY_SCHEMA) == {"category": "a"}
    assert calls == ["claude", "codex"]
    assert store.cooldown_until("claude") == 0.0

def test_claude_command_uses_light_flags(tmp_path):
    seen = []
    def runner(cmd, **kw):
        seen.append(cmd); return ok(cmd, {"category": "a"})
    Chain(Store(tmp_path / "db"), ("claude",), 60, runner=runner).run("p", CLASSIFY_SCHEMA)
    cmd = seen[0]
    assert "--tools" in cmd and "--setting-sources" in cmd and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--disable-slash-commands" in cmd and "--no-session-persistence" in cmd

def test_codex_command_skips_user_config(tmp_path):
    seen = []
    def runner(cmd, **kw):
        seen.append(cmd)
        open(cmd[cmd.index("-o") + 1], "w").write(json.dumps({"category": "a"}))
        return cp()
    Chain(Store(tmp_path / "db"), ("codex",), 60, runner=runner).run("p", CLASSIFY_SCHEMA)
    assert "--ephemeral" in seen[0] and "--ignore-user-config" in seen[0]
    assert seen[0][seen[0].index("--sandbox") + 1] == "read-only"


def test_note_prompt_marks_sources_untrusted():
    p = note_prompt(caption="c", transcript="t", screen_text="s", dm_texts=[], memos=[], categories=[], fixed_category=None)
    assert "untrusted data" in p


def test_valid_note_mentioning_login_is_accepted(tmp_path):
    calls = []
    note = {**NOTE, "title": "grok login 하는 법 (not logged in 해결)"}
    def runner(cmd, **kw):
        calls.append(cmd[0]); return cp(out=json.dumps({"structured_output": note}))
    assert Chain(Store(tmp_path / "db"), ("claude", "codex"), 60, runner=runner).run("p", NOTE_SCHEMA) == note
    assert calls == ["claude"]


def test_timeout_moves_to_next_provider(tmp_path):
    def runner(cmd, **kw):
        if cmd[0] == "claude":
            raise subprocess.TimeoutExpired(cmd, 900)
        return ok(cmd, {"category": "a"})
    assert Chain(Store(tmp_path / "db"), ("claude", "codex"), 60, runner=runner).run("p", CLASSIFY_SCHEMA) == {"category": "a"}
