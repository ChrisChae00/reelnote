import os
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

    def pull(self) -> None:
        res = self._git("pull", "--rebase", "--autostash", "-q", check=False)
        while res.returncode != 0:
            conflicts = self._git("diff", "--name-only", "--diff-filter=U").stdout.split()
            if conflicts != [CATEGORIES]:
                self._git("rebase", "--abort", check=False)
                raise SyncError(f"pull conflict in {conflicts or res.stderr.strip()}")
            # during rebase "ours" is the remote side: the phone's categories.md wins
            self._git("checkout", "--ours", "--", CATEGORIES)
            self._git("add", CATEGORIES)
            res = self._git("rebase", "--continue", check=False)

    def inbox_files(self) -> list[Path]:
        return sorted((self.path / "inbox").glob("*.json"))

    def commit(self, message: str) -> bool:
        self._git("add", "-A")
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        self._git("commit", "-q", "-m", message)
        return True

    def push(self) -> None:
        for _ in range(3):
            if self._git("push", "-q", "origin", "HEAD", check=False).returncode == 0:
                return
            self.pull()
        raise SyncError("push rejected 3 times")
