"""Shared pieces of the pipeline: build log, command runner, errors and paths."""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import threading
import time

PIPELINE_VERSION = "0.1.0"
WORKER_ROOT = os.environ.get("WORKER_ROOT", "/opt/worker")
GAME_DIR = os.environ.get("WRECKFEST_DIR", "/wreckfest")
BLENDER = [os.environ.get("BLENDER", "blender"), "-b", "--factory-startup", "--python-exit-code", "1"]
TOOL_TIMEOUT = int(os.environ.get("PIPELINE_TOOL_TIMEOUT", "900"))

# Lines that look like errors but are known to be harmless (see worker/README.md, "Findings from bgeometry").
KNOWN_NOTICES = [
    (re.compile(r"illegal key (otherway|crossstart)", re.I),
     "bgeometry reports 'custom data has illegal key {0}' but applies the key (known notice)"),
    (re.compile(r"XDG_RUNTIME_DIR"), "Wine: XDG_RUNTIME_DIR is invalid (harmless)"),
    (re.compile(r"BKE_icon"), "Blender: BKE_icon messages while Toolbox registers (harmless)"),
]
IGNORED = re.compile(r"^\s*$|Log created")
ERROR_RE = re.compile(r"(^|[^a-z])(error|failed|warning|fatal|assert)", re.I)


class BuildError(Exception):
    """A build failure with a short message for the API."""


class Cancelled(Exception):
    pass


class BuildLog:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.info: dict[str, int] = {}
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.lock = threading.Lock()

    def line(self, text: str) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
        with self.lock:
            self.parts.append(f"[{stamp}] {text}")
        print(text, flush=True)

    def section(self, title: str, body: str) -> None:
        with self.lock:
            self.parts.append(f"----- {title} -----\n{body.rstrip()}\n----- end {title} -----")

    def notice(self, line: str) -> bool:
        """File a known notice under info. Returns True when the line matched one."""
        for rx, text in KNOWN_NOTICES:
            m = rx.search(line)
            if m:
                key = text.format(*(g.lower() for g in m.groups()))
                self.info[key] = self.info.get(key, 0) + 1
                return True
        return False

    def error(self, text: str) -> None:
        self.errors.append(text)
        self.line(f"ERROR {text}")

    def warn(self, text: str) -> None:
        self.warnings.append(text)

    def render(self) -> str:
        out = ["== trackgen build log ==", *self.parts, "", "== info (known notices) =="]
        out += [f"{k} (x{n})" for k, n in sorted(self.info.items())] or ["none"]
        out += ["", "== warnings =="] + (self.warnings or ["none"])
        out += ["", "== errors =="] + (self.errors or ["none"])
        return "\n".join(out) + "\n"


class Runner:
    """Runs commands with a timeout and cooperative cancellation; output goes to the build log."""

    def __init__(self, log: BuildLog, cancel: threading.Event | None = None) -> None:
        self.log = log
        self.cancel = cancel or threading.Event()

    def run(self, title: str, cmd: list[str], cwd: str | None = None, timeout: int = TOOL_TIMEOUT) -> tuple[int, str]:
        if self.cancel.is_set():
            raise Cancelled()
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace", start_new_session=True)
        chunks: list[str] = []
        reader = threading.Thread(target=lambda: chunks.append(proc.stdout.read()), daemon=True)
        reader.start()
        while proc.poll() is None:
            if self.cancel.is_set() or time.time() - t0 > timeout:
                proc.kill()
                proc.wait()
                reader.join(5)
                if self.cancel.is_set():
                    raise Cancelled()
                self.log.section(f"{title} (timed out)", "".join(chunks))
                raise BuildError(f"{title}: timed out after {timeout} s")
            time.sleep(0.2)
        reader.join(30)
        out = "".join(chunks)
        self.log.section(f"{title} (exit {proc.returncode}, {time.time() - t0:.1f} s)", out)
        return proc.returncode, out
