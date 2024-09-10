# -*- coding: utf-8 -*-
import itertools
import os
import signal
import subprocess
import sys
import threading
from enum import Enum
from typing import Any, Optional

# Script to run multiple workers in parallel as sub-processes. Blocks until all
# sub-processes have exited. Sends SIGTERM to all workers on SIGINT, and
# SIGKILL on second SIGINT.
#
# NB(bruno): my bash foo wasn't strong enough to make this happen in a script
# that worked well across macos and linux. Python it is ¯\_(ツ)_/¯


class Color(Enum):
    # ANSI color codes
    RED = 31
    GREEN = 32
    YELLOW = 33
    BLUE = 34
    MAGENTA = 35
    CYAN = 36


_COLOR_RESET = 0


def _log(msg: str) -> None:
    print(msg)


class SubprocRunner:
    _proc: Optional[subprocess.Popen[str]] = None

    def __init__(self, name: str, color: Color, cmd: list[str]) -> None:
        self.name = name
        self.color = color
        self.cmd = cmd

    def run(self) -> None:
        if self._proc is not None:
            return None

        self._proc = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setpgrp,
        )

        _log(f"🚀 Subprocess {self.name} running (pid={self._proc.pid}).")
        while True:
            assert self._proc.stdout is not None
            output = self._proc.stdout.readline()
            if output == "" and self._proc.poll() is not None:
                break
            if output:
                self._log(output.strip())

        emoji = "🟢" if self._proc.returncode == 0 else "🔴"
        _log(f"{emoji} Subprocess {self.name} exited with code {self._proc.returncode}")

    def stop(self, kill: bool = False) -> None:
        if self._proc is None:
            return None

        if kill:
            self._proc.kill()
        else:
            self._proc.terminate()
            self._proc.wait()

    def _log(self, msg: str) -> None:
        print(f"\033[{self.color.value}m[{self.name}] \033[{_COLOR_RESET}m{msg}")


sigints: int = 0
subprocs: list[SubprocRunner] = []


def stop_subprocs(sig: int, _: Any) -> None:
    """Handle SIGINT signal by sending SIGTERM or SIGKILL to child processes."""
    global sigints
    sigints += 1
    if sigints > 1:
        _log("\n🪓 Sending SIGKILL to workers...")
        for process in subprocs:
            process.stop(kill=True)
        sys.exit(1)
    else:
        _log("\n✋ Sending SIGTERM to workers (ctrl+c again to send SIGKILL)...")
        for process in subprocs:
            process.stop()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: poetry run-workers <worker1> <worker2> ...")
        sys.exit(1)

    # Install SIGINT handler to send SIGTERM to child processes.
    # This'll prevent KeyboardInterrupt from raising below.
    signal.signal(signal.SIGINT, stop_subprocs)

    workers: list[str] = sys.argv[1:]
    threads: list[threading.Thread] = []

    color_cycle = itertools.cycle([color for color in Color])
    try:
        for worker in workers:
            runner = SubprocRunner(
                worker,
                next(color_cycle),
                ["poetry", "run", "python3", "-u", "-m", f"workers.{worker}"],
            )
            subprocs.append(runner)
            thread = threading.Thread(target=runner.run)
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        pass
