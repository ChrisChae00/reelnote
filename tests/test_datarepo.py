import subprocess
from pathlib import Path
import pytest
from reelnote.datarepo import DataRepo, SyncError

def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)

@pytest.fixture
def remote_and_clones(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True, capture_output=True)
    for c in (seed,):
        git(c, "config", "user.email", "t@t"); git(c, "config", "user.name", "t")
        git(c, "config", "commit.gpgsign", "false")
    (seed / "inbox").mkdir(); (seed / "inbox" / ".gitkeep").write_text("")
    (seed / "vault").mkdir(); (seed / "vault" / "categories.md").write_text("# c\na\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "seed"); git(seed, "push", "-q", "origin", "main")
    mac, phone = tmp_path / "mac", tmp_path / "phone"
    for c in (mac, phone):
        subprocess.run(["git", "clone", "-q", str(remote), str(c)], check=True, capture_output=True)
        git(c, "config", "user.email", "t@t"); git(c, "config", "user.name", "t")
        git(c, "config", "commit.gpgsign", "false")
    return mac, phone

def test_inbox_files_excludes_failed(remote_and_clones):
    mac, _ = remote_and_clones
    (mac / "inbox" / "b.json").write_text("{}"); (mac / "inbox" / "a.json").write_text("{}")
    (mac / "inbox" / "failed").mkdir(); (mac / "inbox" / "failed" / "c.json").write_text("{}")
    assert [p.name for p in DataRepo(mac).inbox_files()] == ["a.json", "b.json"]

def test_commit_nothing_returns_false(remote_and_clones):
    mac, _ = remote_and_clones
    assert DataRepo(mac).commit("noop") is False

def test_push_after_concurrent_phone_commit(remote_and_clones):
    mac, phone = remote_and_clones
    (phone / "vault" / "categories.md").write_text("# c\nb\na\n")
    (phone / "inbox" / "new.json").write_text("{}")
    git(phone, "add", "-A"); git(phone, "commit", "-qm", "phone"); git(phone, "push", "-q", "origin", "main")
    repo = DataRepo(mac)
    (mac / "vault" / "categories.md").write_text("# c\nz\n")
    (mac / "vault" / "note.md").write_text("n")
    assert repo.commit("mac") is True
    repo.push()
    git(phone, "pull", "-q", "--rebase")
    assert (phone / "vault" / "note.md").read_text() == "n"
    assert (phone / "vault" / "categories.md").read_text() == "# c\nb\na\n"
    assert (mac / "inbox" / "new.json").exists()


def _commit_push(c, msg):
    git(c, "add", "-A"); git(c, "commit", "-qm", msg); git(c, "push", "-q", "origin", "main")


def _out(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def _rebasing(c):
    return (c / ".git" / "rebase-merge").exists() or (c / ".git" / "rebase-apply").exists()


def _seed_note(mac, phone, name="note.md"):
    (phone / "vault" / name).write_text("base\n")
    _commit_push(phone, "base")
    git(mac, "pull", "-q", "--rebase")


def test_conflict_outside_categories_keeps_mac_commit(remote_and_clones):
    mac, phone = remote_and_clones
    _seed_note(mac, phone)
    (phone / "vault" / "note.md").write_text("phone\n"); _commit_push(phone, "p")
    (mac / "vault" / "note.md").write_text("mac\n")
    repo = DataRepo(mac)
    assert repo.commit("mac")
    with pytest.raises(SyncError):
        repo.push()
    assert _out(mac, "show", "HEAD:vault/note.md") == "mac\n"
    assert not _rebasing(mac)


def test_dirty_tree_is_committed_not_stashed(remote_and_clones):
    mac, phone = remote_and_clones
    _seed_note(mac, phone, "a.md")
    (phone / "vault" / "a.md").write_text("phone\n"); _commit_push(phone, "p")
    (mac / "vault" / "a.md").write_text("mac-change\n")
    with pytest.raises(SyncError):
        DataRepo(mac).pull()
    assert not _rebasing(mac)
    assert all("<<<<<<<" not in (mac / f).read_text() for f in _out(mac, "ls-files").split())
    assert "mac-change" in _out(mac, "log", "-p", "--all")
    assert _out(mac, "stash", "list") == ""


def test_dirty_tree_non_conflicting_pull_succeeds(remote_and_clones):
    mac, phone = remote_and_clones
    _seed_note(mac, phone, "a.md")
    (phone / "vault" / "b.md").write_text("phone-b\n"); _commit_push(phone, "p")
    (mac / "vault" / "a.md").write_text("mac-change\n")
    DataRepo(mac).pull()
    assert (mac / "vault" / "b.md").read_text() == "phone-b\n"
    assert "mac-change" in _out(mac, "log", "-p")
    assert _out(mac, "status", "--porcelain") == ""


def test_push_failure_surfaces_stderr(remote_and_clones):
    mac, _ = remote_and_clones
    git(mac, "remote", "set-url", "origin", "/nonexistent")
    (mac / "vault" / "x.md").write_text("x")
    repo = DataRepo(mac)
    repo.commit("x")
    with pytest.raises(SyncError, match="push failed"):
        repo.push()


def test_multiple_local_commits_each_conflicting_on_categories(remote_and_clones):
    mac, phone = remote_and_clones
    repo = DataRepo(mac)
    for i in (1, 2):
        (mac / "vault" / "categories.md").write_text(f"# c\nmac{i}\n")
        (mac / "vault" / f"n{i}.md").write_text(str(i))
        assert repo.commit(f"m{i}")
    (phone / "vault" / "categories.md").write_text("# c\nphone\n"); _commit_push(phone, "p")
    repo.push()
    git(phone, "pull", "-q", "--rebase")
    assert (phone / "vault" / "n1.md").exists() and (phone / "vault" / "n2.md").exists()
    assert (phone / "vault" / "categories.md").read_text() == "# c\nphone\n"


def test_stale_rebase_is_recovered(remote_and_clones):
    mac, phone = remote_and_clones
    (phone / "vault" / "categories.md").write_text("# c\nphone\n"); _commit_push(phone, "p")
    (mac / "vault" / "categories.md").write_text("# c\nmac\n")
    (mac / "vault" / "keep.md").write_text("keep")
    git(mac, "add", "-A"); git(mac, "commit", "-qm", "mac")
    subprocess.run(["git", "-C", str(mac), "pull", "--rebase", "-q"], capture_output=True)
    assert _rebasing(mac)
    DataRepo(mac).pull()
    assert not _rebasing(mac)
    assert (mac / "vault" / "categories.md").read_text() == "# c\nphone\n"
    assert (mac / "vault" / "keep.md").read_text() == "keep"
    assert _out(mac, "status", "--porcelain") == ""


def test_commit_skips_atomic_write_temp_files(remote_and_clones):
    mac, _ = remote_and_clones
    (mac / "vault" / "notes").mkdir()
    (mac / "vault" / "notes" / ".a.md.x1y2.tmp").write_text("half")
    (mac / "vault" / ".review.md.abc.tmp").write_text("half")
    (mac / "vault" / "notes" / "a.md").write_text("done")
    assert DataRepo(mac).commit("c")
    files = subprocess.run(["git", "-C", str(mac), "ls-files"], capture_output=True, text=True).stdout.split()
    assert "vault/notes/a.md" in files and not [f for f in files if f.endswith(".tmp")]


def test_remote_head(remote_and_clones, tmp_path):
    from reelnote.datarepo import DataRepo
    mac, _phone = remote_and_clones
    r = DataRepo(mac)
    assert r.remote_head() == r.local_head()
    git(mac, "remote", "set-url", "origin", "https://nonexistent.invalid/x.git")
    assert r.remote_head() is None  # DNS failure counts as offline
    assert DataRepo(tmp_path / "missing").remote_head() == ""
