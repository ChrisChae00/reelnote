import argparse
import fcntl
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from reelnote.config import load
from reelnote.datarepo import OFFLINE, DataRepo, SyncError
from reelnote.store import Store

FULL_RUN_EVERY = 900  # seconds; items left in the inbox (AI limit, failed download) are retried this often


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
    if os.environ.get("REELNOTE_NO_NOTIFY") == "1" or not shutil.which("osascript"):
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
        if args.command == "run":
            # launchd starts this every minute: a cheap ls-remote decides whether the full run is needed
            head = repo.remote_head()
            if head is None:
                return 0  # offline: stay quiet, the next minute tries again
            # "" = ls-remote failed some other way: run every FULL_RUN_EVERY so the run reports it, not every minute
            if head in ("", store.get_meta("seen_head")) and time.time() - store.get_meta("last_full_run", 0) < FULL_RUN_EVERY:
                return 0
            if head:
                store.set_meta("seen_head", head)
            store.set_meta("last_full_run", time.time())
        from reelnote import pipeline  # heavy imports (~0.7 s CPU), only when there is work
        from reelnote.llm import AllProvidersLimited
        deps = pipeline.Deps.real(cfg, store)
        try:
            if args.command == "run":
                print(pipeline.run_once(cfg, store, deps, repo))
                store.set_meta("seen_head", repo.local_head())  # our own push is not new work
            else:
                repo.pull()
                print(f"new hubs: {pipeline.regroup(cfg, store, deps)}")
                pipeline.write_reports(cfg, store)
                repo.commit("reelnote: regroup")
                repo.push()
        except SyncError as e:
            print(f"reelnote: sync failed: {e}", file=sys.stderr)
            if not OFFLINE.search(str(e)):  # Wi-Fi dropped mid-run: the next run retries
                notify(f"sync failed: {e}")
            return 1
        except AllProvidersLimited as e:  # only regroup lets this escape; run_once handles it per item
            print(f"reelnote: no AI provider available: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
