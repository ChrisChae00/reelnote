import argparse
import fcntl
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from reelnote import pipeline
from reelnote.config import load
from reelnote.datarepo import DataRepo, SyncError
from reelnote.llm import AllProvidersLimited
from reelnote.store import Store


@contextmanager
def run_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "run.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def notify(message: str) -> None:
    """Best-effort macOS banner: launchd runs are otherwise silent."""
    if not shutil.which("osascript"):
        return
    try:  # message goes in as argv, never spliced into the script
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        'display notification (item 1 of argv) with title "reelnote"', "-e", "end run", message],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reelnote")
    parser.add_argument("command", choices=["run", "regroup", "status"])
    args = parser.parse_args(argv)
    cfg = load()
    store = Store(cfg.db_path)
    if args.command == "status":
        counts: dict[str, int] = {}
        for item in store.all():
            counts[item.state] = counts.get(item.state, 0) + 1
        for state, n in sorted(counts.items()):
            print(f"{state}: {n}")
        for p in cfg.providers:
            until = store.cooldown_until(p)
            if until > time.time():
                print(f"{p}: limited until {time.strftime('%H:%M', time.localtime(until))}")
        return 0
    with run_lock(cfg.state_dir) as got:
        if not got:
            print("another reelnote run is in progress", file=sys.stderr)
            return 0
        repo = DataRepo(cfg.data_repo)
        deps = pipeline.Deps.real(cfg, store)
        try:
            if args.command == "run":
                print(pipeline.run_once(cfg, store, deps, repo))
            else:
                repo.pull()
                print(f"new hubs: {pipeline.regroup(cfg, store, deps)}")
                pipeline.write_reports(cfg, store)
                repo.commit("reelnote: regroup")
                repo.push()
        except SyncError as e:
            print(f"reelnote: sync failed: {e}", file=sys.stderr)
            notify(f"sync failed: {e}")
            return 1
        except AllProvidersLimited as e:  # only regroup lets this escape; run_once handles it per item
            print(f"reelnote: no AI provider available: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
