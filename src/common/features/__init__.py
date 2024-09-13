# -*- coding: utf-8 -*-
import os
import threading
from typing import Optional

import ldclient
from ldclient.integrations import Files

from .features import Features
from .launchdarkly import LDFeatures

_features: Optional[Features] = None
_lock = threading.Lock()


def _init_features() -> Features:
    env = os.getenv("ENV", "local")

    if env in ("dev", "prod"):
        cfg = ldclient.Config(sdk_key=os.environ["LAUNCHDARKLY_SDK_KEY"])
    else:
        data_source_callback = Files.new_data_source(
            paths=["feature-flags.yaml"],
            auto_update=True,
        )
        cfg = ldclient.Config(
            "fake-key",
            update_processor_class=data_source_callback,
            send_events=False,
            diagnostic_opt_out=True,
        )

    return LDFeatures(config=cfg)


def get() -> Features:
    """
    Returns the singleton instance of the Features class,
    initializing it if necessary.
    """
    global _features
    global _lock

    if _features is not None:
        return _features

    with _lock:
        if _features is None:
            _features = _init_features()
    return _features
