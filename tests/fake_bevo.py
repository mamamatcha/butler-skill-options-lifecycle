"""A scripted stand-in for the `bevo` SDK, for running duty.py offline.

`run(...)` installs a fresh fake as `bevo`, sets PARAMS, pins the clock to each
tick's time, intercepts `subprocess.run`, executes duty.py to the end of its
ticks (or to `bevo.done()`), and returns the fake for assertions.
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

EXPIRY = 1793347200  # ETH-20261030-2450-P: 2026-10-30T08:00:00Z

PUT = {
    "INSTRUMENT": "ETH-20261030-2450-P",
    "PRODUCT": "cash_secured_put",
    "UNDERLYING": "ETH",
    "STRIKE": 2450,
    "SIZE": 2.04,
    "PREMIUM_USD": 55.17,
    "COLLATERAL_USD": 4998,
}

CALL = {
    "INSTRUMENT": "ETH-20261030-3000-C",
    "PRODUCT": "covered_call",
    "UNDERLYING": "ETH",
    "STRIKE": 3000,
    "SIZE": 1.5,
    "PREMIUM_USD": 40.25,
}


class BevoError(Exception):
    pass


class FakeBevo:
    BevoError = BevoError
    SERVICE_ID = "svc-1"

    def __init__(self, times, reads, acp, statuses, state):
        self.times = list(times)
        self.reads = dict(reads)
        self.acp_replies = list(acp)
        self.statuses = dict(statuses)
        self.state = dict(state)
        self.now = None
        self.logs, self.notes, self.fails, self.dones = [], [], [], []
        self.read_calls, self.acp_calls = [], []

    def log(self, message):
        self.logs.append(str(message))

    def notify(self, text, quiet=False, push=None):
        self.notes.append(str(text))
        return {"ok": True}

    def fail(self, reason):
        self.fails.append(str(reason))

    def done(self, summary=None):
        self.dones.append(summary)
        raise SystemExit(0)

    def read(self, path, params=None):
        self.read_calls.append((path, dict(params or {})))
        value = self.reads.get(path)
        if callable(value):
            value = value(params or {})
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise BevoError("no fixture for %s" % path)
        return value

    def exec_status(self, key, route="trade"):
        return self.statuses.get(key, {"state": "not_found"})

    def ticks(self):
        for at in self.times:
            self.now = at
            yield {"kind": "timer"}

    def _run(self, argv, **_kwargs):
        self.acp_calls.append(list(argv))
        stdout = self.acp_replies.pop(0) if self.acp_replies else ""
        return subprocess.CompletedProcess(argv, 0, stdout, "")


def run(params, times, reads=None, acp=(), statuses=None, state=None, name="ETH note"):
    fake = FakeBevo(times, reads or {}, acp, statuses or {}, state or {})
    sys.modules["bevo"] = fake
    os.environ["PARAMS"] = json.dumps(params)
    os.environ["BEVO_SERVICE_NAME"] = name
    spec = importlib.util.spec_from_file_location("duty_under_test", ROOT / "duty.py")
    module = importlib.util.module_from_spec(spec)
    real_time = time.time
    with mock.patch("time.time", lambda: fake.now if fake.now is not None else real_time()), \
            mock.patch("subprocess.run", fake._run):
        try:
            spec.loader.exec_module(module)
        except SystemExit:
            pass
    return fake


def hours(n):
    return int(n * 3600)


def answer(**fields):
    return json.dumps(fields)
