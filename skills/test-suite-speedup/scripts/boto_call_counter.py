"""pytest plugin: count real botocore API calls per test (service, operation, region, seconds).

Usage (from the repo root, plugin dir on PYTHONPATH):
    PYTHONPATH=<dir of this file> pytest -p boto_call_counter -q <targets>
Writes boto_per_test.json next to this file (override with BOTO_COUNTER_OUT).
Any call recorded here left the process: the tests depend on live cloud state and ambient credentials.
"""

import collections
import json
import os
import time

import pytest

_calls = collections.defaultdict(list)
_current = {"id": None}


def pytest_configure(config):
    from botocore.client import BaseClient

    real = BaseClient._make_api_call

    def counted(self, operation_name, api_params):
        t0 = time.time()
        try:
            return real(self, operation_name, api_params)
        finally:
            _calls[_current["id"]].append(
                (self.meta.service_model.service_name, operation_name, self.meta.region_name, round(time.time() - t0, 3))
            )

    BaseClient._make_api_call = counted


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _current["id"] = item.nodeid
    yield
    _current["id"] = None


def pytest_sessionfinish(session, exitstatus):
    out = os.environ.get("BOTO_COUNTER_OUT") or os.path.join(os.path.dirname(__file__), "boto_per_test.json")
    json.dump({k or "<outside a test>": v for k, v in _calls.items()}, open(out, "w"))
