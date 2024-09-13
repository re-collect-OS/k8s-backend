# -*- coding: utf-8 -*-
import signal
from typing import Any

from loguru import logger


class OSSignalHandler:
    """Installs SIGTERM and SIGINT handlers to flip a bool flag."""

    term_received: bool = False

    def __init__(self):
        signal.signal(signal.SIGTERM, self._handle_signals)
        signal.signal(signal.SIGINT, self._handle_signals)

    def _handle_signals(self, signum: int, _: Any) -> None:
        sig_name = signal.Signals(signum).name
        self.term_received = True

        logger.info(f"Received {sig_name}, terminating...")
