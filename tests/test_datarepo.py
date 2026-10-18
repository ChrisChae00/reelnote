import subprocess
from pathlib import Path
import pytest
from reelnote.datarepo import DataRepo

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
