# newscentral/supervisor.py
# Starts the worker process and restarts it after a crash, with a delay that doubles from
# min_delay to max_delay and goes back to min_delay after the worker has run for reset_after.
import subprocess
import threading
import time

EXIT_SCHEMA = 3    # worker exit code: database and code disagree on the schema; do not restart


def next_delay(previous, ran_for, min_delay, max_delay, reset_after):
    if previous <= 0 or ran_for >= reset_after:
        return min_delay
    return min(previous * 2, max_delay)


def _log(*parts):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "supervisor:", *parts, flush=True)


class Supervisor(threading.Thread):
    def __init__(self, cmd, cwd=None, stdout=None, min_delay=10, max_delay=600, reset_after=600, log=_log):
        super().__init__(daemon=True)
        self.cmd = list(cmd)
        self.cwd = cwd
        self.stdout = stdout
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.reset_after = reset_after
        self.log = log
        self.proc = None
        self.starts = 0
        self.delays = []
        self.gave_up = False
        self._stopping = threading.Event()
        self._lock = threading.Lock()

    def run(self):
        delay = 0
        while not self._stopping.is_set():
            started = time.monotonic()
            try:
                with self._lock:
                    if self._stopping.is_set():
                        break
                    self.proc = subprocess.Popen(
                        self.cmd, cwd=self.cwd, stdout=self.stdout,
                        stderr=subprocess.STDOUT if self.stdout is not None else None)
                    self.starts += 1
                code = self.proc.wait()
            except OSError as e:
                self.log("could not start the worker:", e)
                code = None
            if self._stopping.is_set():
                break
            if code == EXIT_SCHEMA:
                self.gave_up = True
                self.log("worker refused the database (schema version); not restarting it")
                break
            delay = next_delay(delay, time.monotonic() - started, self.min_delay, self.max_delay,
                               self.reset_after)
            self.delays.append(delay)
            self.log("worker exited with code %s; restarting in %s s" % (code, delay))
            self._stopping.wait(delay)

    def stop(self, timeout=5):
        """Terminate the worker and stop restarting it. Results are written in single
        transactions, so killing the worker mid-batch loses at most that batch."""
        self._stopping.set()
        with self._lock:
            proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
