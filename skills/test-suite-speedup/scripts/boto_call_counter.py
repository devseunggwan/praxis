"""pytest plugin: count real botocore API calls per test (service, operation, region, seconds).

Usage (from the repo root, plugin dir on PYTHONPATH):
    PYTHONPATH=<dir of this file> pytest -p boto_call_counter -q <targets>
Writes boto_per_test.json next to this file (override with BOTO_COUNTER_OUT).
A call is recorded only when botocore handed its request to the HTTP layer (Endpoint._send):
calls answered by a Stubber or a before-send mock (moto), and calls that fail before the
send (no credentials, parameter validation), are not counted. Any call recorded here left
the process: the tests depend on live cloud state and ambient credentials.
Under pytest-xdist each worker hands its calls to the controller, which writes one merged report.
"""

import collections
import json
import os
import time

import pytest

_calls = collections.defaultdict(list)
_current = {"id": None}
_sent = {"flag": False}


def pytest_configure(config):
    from botocore.client import BaseClient
    from botocore.endpoint import Endpoint

    real_call = BaseClient._make_api_call
    real_send = Endpoint._send

    def send(self, request):
        _sent["flag"] = True
        return real_send(self, request)

    def counted(self, operation_name, api_params):
        t0 = time.time()
        outer, _sent["flag"] = _sent["flag"], False
        try:
            return real_call(self, operation_name, api_params)
        finally:
            if _sent["flag"]:
                _calls[_current["id"]].append(
                    (self.meta.service_model.service_name, operation_name, self.meta.region_name,
                     round(time.time() - t0, 3))
                )
            _sent["flag"] = outer

    Endpoint._send = send
    BaseClient._make_api_call = counted


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _current["id"] = item.nodeid
    yield
    _current["id"] = None


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    # xdist controller: merge the calls a finished worker handed over in workeroutput.
    for k, v in getattr(node, "workeroutput", {}).get("boto_calls", {}).items():
        _calls[k].extend(v)


def pytest_sessionfinish(session, exitstatus):
    data = {k or "<outside a test>": v for k, v in _calls.items()}
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:  # xdist worker: the controller writes the report
        workeroutput["boto_calls"] = data
        return
    out = os.environ.get("BOTO_COUNTER_OUT") or os.path.join(os.path.dirname(__file__), "boto_per_test.json")
    with open(out, "w") as f:
        json.dump(data, f)
