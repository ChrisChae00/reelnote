import os
import re
import subprocess
from pathlib import Path

CATEGORIES = "vault/categories.md"


class SyncError(Exception):
    pass


class DataRepo:
    def __init__(self, path: Path):
        self.path = path

    def _git(self, *args, check=True) -> subprocess.CompletedProcess:
        env = {**os.environ, "GIT_EDITOR": "true"}
        res = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, env=env)
        if check and res.returncode != 0:
            raise SyncError(f"git {' '.join(args)}: {res.stderr.strip()}")
        return res

    def _rebasing(self) -> bool:
        gd = Path(self._git("rev-parse", "--git-dir").stdout.strip())
        gd = gd if gd.is_absolute() else self.path / gd
        return (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists()

    def _unmerged(self) -> list[str]:
        out = self._git("diff", "--name-only", "-z", "--diff-filter=U").stdout
        return [p for p in out.split("\0") if p]

    def _abort_if_rebasing(self) -> None:
        if self._rebasing():
            self._git("rebase", "--abort", check=False)

    def pull(self) -> None:
        self._abort_if_rebasing()  # recover from a crashed run
        self.commit("reelnote: save uncommitted changes")  # commit, never stash
        res = self._git("pull", "--rebase", "-q", check=False)
        steps = 0
        while res.returncode != 0:
            conflicts = self._unmerged()
            if not conflicts:
                self._abort_if_rebasing()
                raise SyncError(f"pull failed: {res.stderr.strip()}")
            if conflicts != [CATEGORIES]:
                self._abort_if_rebasing()
                raise SyncError(f"pull conflict in {conflicts}")
            if steps == 50:
                self._abort_if_rebasing()
                raise SyncError("rebase did not finish after 50 steps")
            steps += 1
            # during rebase "ours" is the remote side: the phone's categories.md wins
            self._git("checkout", "--ours", "--", CATEGORIES)
            self._git("add", CATEGORIES)
            res = self._git("rebase", "--continue", check=False)
        if self._unmerged():
            raise SyncError(f"unresolved conflicts after pull: {self._unmerged()}")

    def inbox_files(self) -> list[Path]:
        return sorted((self.path / "inbox").glob("*.json"))

    def commit(self, message: str) -> bool:
        self._git("add", "-A", "--", ".", ":(exclude,glob)**/.*.tmp")  # never a half-written atomic write
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        self._git("commit", "-q", "-m", message)
        return True

    def push(self) -> None:
        err = ""
        for _ in range(3):
            res = self._git("push", "-q", "origin", "HEAD", check=False)
            if res.returncode == 0:
                return
            err = res.stderr.strip()
            if not re.search(r"rejected|fetch first|non-fast-forward", err, re.I):
                raise SyncError(f"push failed: {err}")
            self.pull()
        raise SyncError("push rejected 3 times: " + err)
