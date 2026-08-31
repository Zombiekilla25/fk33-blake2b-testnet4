#!/usr/bin/env python3

import importlib.util
import json
import socket
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "software/pyblock-carousel/fk33_b2b_pyblock_production_client_v8_devfee.py"
TRANSLATOR = ROOT / "software/pyblock-carousel/fk33_b2b_sia_f2pool_translator_v2.py"
SPEC = importlib.util.spec_from_file_location("fk33_client_under_test", CLIENT)
assert SPEC is not None and SPEC.loader is not None
fk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fk)
TRANSLATOR_SPEC = importlib.util.spec_from_file_location(
    "translator_under_test", TRANSLATOR
)
assert TRANSLATOR_SPEC is not None and TRANSLATOR_SPEC.loader is not None
real_translator = importlib.util.module_from_spec(TRANSLATOR_SPEC)
TRANSLATOR_SPEC.loader.exec_module(real_translator)


class FakeTranslator:
    @staticmethod
    def parse_subscribe(message):
        if message.get("id") != 1:
            raise ValueError("subscription response id must be one")
        if message.get("result") != ["subscription", "00000000", 8]:
            raise ValueError("bad subscription")
        return "00", 8

    @staticmethod
    def parse_difficulty(value):
        return float(value)

    @staticmethod
    def parse_notify(message):
        if not isinstance(message.get("params"), list):
            raise ValueError("bad notify")
        return message["params"]


class MockStratum(threading.Thread):
    def __init__(self, fixture=None):
        super().__init__(daemon=True)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.authorized_username = None
        self.error = None
        self.fixture = fixture

    def run(self):
        try:
            conn, _ = self.listener.accept()
            with conn:
                stream = conn.makefile("rwb")
                subscribe = json.loads(stream.readline())
                authorize = json.loads(stream.readline())
                self.authorized_username = authorize["params"][0]
                if self.fixture is None:
                    replies = [
                        {"id": subscribe["id"], "result": ["subscription", "00000000", 8], "error": None},
                        {"id": authorize["id"], "result": True, "error": None},
                        {"id": None, "method": "mining.set_difficulty", "params": [16384]},
                        {"id": None, "method": "mining.notify", "params": ["job"]},
                    ]
                else:
                    fixture_subscribe, fixture_difficulty, fixture_notify = self.fixture
                    fixture_subscribe = dict(fixture_subscribe)
                    fixture_subscribe["id"] = subscribe["id"]
                    replies = [
                        fixture_subscribe,
                        {"id": authorize["id"], "result": True, "error": None},
                        {"id": None, "method": "mining.set_difficulty", "params": [fixture_difficulty]},
                        fixture_notify,
                    ]
                for reply in replies:
                    stream.write(json.dumps(reply).encode() + b"\n")
                stream.flush()
                time.sleep(0.2)
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            self.error = exc
        finally:
            self.listener.close()


class DevFeeTests(unittest.TestCase):
    def test_exact_one_percent_and_deterministic_cycle(self):
        scheduler = fk.DevFeeScheduler(100, 1)
        roles = [scheduler.choose(True) for _ in range(10000)]
        self.assertEqual(roles.count("developer"), 100)
        self.assertTrue(all(roles[index] == "developer" for index in range(0, 10000, 100)))
        self.assertEqual(scheduler.snapshot()["actual_percent"], 1.0)

    def test_fail_open_does_not_catch_up(self):
        scheduler = fk.DevFeeScheduler(100, 1)
        first = [scheduler.choose(False) for _ in range(100)]
        second = [scheduler.choose(True) for _ in range(100)]
        self.assertEqual(set(first), {"user"})
        self.assertEqual(second.count("developer"), 1)
        self.assertEqual(scheduler.snapshot()["fallback_sweeps"], 1)

    def test_second_session_uses_disclosed_worker(self):
        server = MockStratum()
        server.start()
        auxiliary = fk.AuxiliaryStratum(
            "127.0.0.1",
            server.port,
            fk.DEFAULT_DEVFEE_USERNAME,
            FakeTranslator,
            10,
            1.0,
            10.0,
        )
        try:
            self.assertTrue(auxiliary.maybe_connect())
            self.assertTrue(auxiliary.ready)
            self.assertEqual(auxiliary.state["difficulty"], 16384.0)
            self.assertEqual(auxiliary.username, fk.DEFAULT_DEVFEE_USERNAME)
        finally:
            auxiliary.close()
            server.join(timeout=2)
        self.assertIsNone(server.error)
        self.assertEqual(server.authorized_username, fk.DEFAULT_DEVFEE_USERNAME)

    def test_unavailable_second_session_fails_open(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        auxiliary = fk.AuxiliaryStratum(
            "127.0.0.1",
            port,
            fk.DEFAULT_DEVFEE_USERNAME,
            FakeTranslator,
            10,
            0.5,
            10.0,
        )
        self.assertFalse(auxiliary.maybe_connect())
        self.assertFalse(auxiliary.ready)
        self.assertEqual(auxiliary.connect_failures, 1)
        scheduler = fk.DevFeeScheduler()
        self.assertEqual(scheduler.choose(auxiliary.ready), "user")

    def test_real_translator_accepts_developer_handshake_ids(self):
        server = MockStratum(real_translator.fixture_messages())
        server.start()
        auxiliary = fk.AuxiliaryStratum(
            "127.0.0.1",
            server.port,
            fk.DEFAULT_DEVFEE_USERNAME,
            real_translator,
            10,
            1.0,
            10.0,
        )
        try:
            self.assertTrue(auxiliary.maybe_connect())
            self.assertTrue(auxiliary.ready)
        finally:
            auxiliary.close()
            server.join(timeout=2)
        self.assertIsNone(server.error)


if __name__ == "__main__":
    unittest.main()
