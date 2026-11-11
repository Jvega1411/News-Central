# newscentral/worker.py
# Batch-job worker. Runs as its own process: python -m newscentral.worker --config <path>.
# The server starts and supervises it (supervisor.py). Migrations never run here: the database
# must already be at the schema version of this code.
import argparse
import collections
import os
import signal
import sqlite3
import sys
import threading
import time

from .config import CONFIG_FILE, ConfigError, load_config, paths
from .store import SchemaMismatch, Store
from .supervisor import EXIT_SCHEMA

IDLE_SECONDS = 30
PARENT_CHECK_SECONDS = 5


def parent_alive(pid):
    """True while process `pid` (the server that started this worker) is still running."""
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x00100000, False, pid)     # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) != 0   # 0 == WAIT_OBJECT_0: exited
        finally:
            kernel32.CloseHandle(handle)
    return os.getppid() == pid


def log(*parts):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "worker:", *parts, flush=True)


class Worker:
    def __init__(self, store, jobs, idle_seconds=IDLE_SECONDS, clock=time.time, parent_check=None):
        self.store = store
        self.jobs = list(jobs)
        self.idle_seconds = idle_seconds
        self.clock = clock
        self.parent_check = parent_check      # callable -> bool, or None (manual run: no check)
        self.stopping = threading.Event()
        self.done = {job.name: collections.deque() for job in self.jobs}   # (time, units) per batch
        self.last_batch = {job.name: None for job in self.jobs}

    def stop(self):
        self.stopping.set()

    def done_last_hour(self, name, now):
        q = self.done[name]
        while q and q[0][0] < now - 3600:
            q.popleft()
        return sum(n for _, n in q)

    def status(self, job, state, error=""):
        now = self.clock()
        try:
            self.store.set_worker_status(job.name, state, self.last_batch[job.name],
                                         self.done_last_hour(job.name, now), error, now)
        except sqlite3.Error as e:
            log("could not write status:", e)

    def step(self):
        """Run one batch of the first job that has work. Returns True when a batch was saved."""
        for job in self.jobs:
            if self.stopping.is_set():
                return False
            if not job.available():
                self.status(job, "unavailable", job.reason)
                continue
            try:
                batch = job.pending(self.store, job.batch_size)
            except sqlite3.Error as e:
                self.status(job, "error", "reading the queue: %s" % e)
                continue
            if not batch:
                self.status(job, "idle")
                continue
            try:
                results = job.run(batch)
            except Exception as e:
                log(job.name, "batch failed:", e)
                self.status(job, "error", "%s: %s" % (type(e).__name__, e))
                continue
            try:
                done = job.save(self.store, batch, results)
            except sqlite3.Error as e:
                # Locked beyond the busy timeout, or any other write error: the transaction rolled
                # back, nothing was written, and the same batch comes back on the next loop.
                self.status(job, "error", "saving: %s" % e)
                continue
            now = self.clock()
            self.last_batch[job.name] = now
            self.done[job.name].append((now, done))
            self.status(job, "running")
            return True
        return False

    def idle_wait(self):
        """Sleep up to idle_seconds, waking at least every PARENT_CHECK_SECONDS to look for the parent."""
        end = time.monotonic() + self.idle_seconds
        while not self.stopping.is_set():
            left = end - time.monotonic()
            if left <= 0:
                return
            self.stopping.wait(min(left, PARENT_CHECK_SECONDS))
            if self.parent_check is not None and not self.parent_check():
                return

    def run_forever(self):
        log("started; jobs:", ", ".join(j.name for j in self.jobs) or "none")
        while not self.stopping.is_set():
            if self.parent_check is not None and not self.parent_check():
                log("parent process is gone; exiting")
                break
            try:
                ran = self.step()
            except Exception as e:
                log("loop error:", e)
                ran = False
            if not ran:
                self.idle_wait()
        for job in self.jobs:
            self.status(job, "stopped")
        log("stopped")


def install_signal_handlers(worker):
    """Stop cleanly on SIGTERM, SIGINT and (Windows) SIGBREAK. Returns the previous handlers."""
    def handler(signum, frame):
        worker.stop()

    previous = {}
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            previous[sig] = signal.signal(sig, handler)
    return previous


def build_jobs(cfg, p):
    """The jobs this worker runs, in priority order."""
    from .translate.job import TranslateJob
    return [TranslateJob(cfg, p["models"])]


def main(argv=None):
    ap = argparse.ArgumentParser(description="News Central batch-job worker")
    ap.add_argument("--config", default=CONFIG_FILE, help="path to config.json")
    ap.add_argument("--once", action="store_true", help="run one loop step and exit")
    ap.add_argument("--parent-pid", type=int, default=None,
                    help="exit when this process is gone (set by the server's supervisor)")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log(e)
        return 2
    p = paths(cfg)
    try:
        store = Store(p["db"], migrate=False)
    except SchemaMismatch as e:
        log(e)
        return EXIT_SCHEMA
    except FileNotFoundError as e:
        log(e)
        return 1
    check = (lambda: parent_alive(args.parent_pid)) if args.parent_pid else None
    worker = Worker(store, build_jobs(cfg, p), parent_check=check)
    previous = install_signal_handlers(worker)
    try:
        if args.once:
            worker.step()
        else:
            worker.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for sig, handler in previous.items():      # matters when main() runs inside the test process
            signal.signal(sig, handler)
        store.db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
