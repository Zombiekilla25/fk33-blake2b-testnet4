#!/usr/bin/env python3
"""Run an automatically supervised FK33 BLAKE2b Sia f2pool/MRR worker."""

from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import select
import socket
import statistics
import sys
import time
from fractions import Fraction
from pathlib import Path
from types import ModuleType
from typing import Any


ADAPTER_SHA256 = (
    "c64d3d207be26ca268e9e12651dc2916331096d8c47359c9f6c58410bf231f1f"
)
TRANSLATOR_SHA256 = (
    "a33160ebd93a6eaa8549d32e730ac9485e1e89333bb6370c52fb2431b01992d1"
)
DIFF1_TARGET = 0x00000000FFFF0000 << 192
EXPECTED_HASHES_PER_DIFF1 = (1 << 256) / (DIFF1_TARGET + 1)
SUBSCRIBE_ID = 1
AUTHORIZE_ID = 2
FIRST_SUBMIT_ID = 1000
DEVFEE_SUBSCRIBE_ID = SUBSCRIBE_ID
DEVFEE_AUTHORIZE_ID = AUTHORIZE_ID
DEVFEE_FIRST_SUBMIT_ID = 101000000
DEFAULT_DEVFEE_USERNAME = (
    "bc1qe77h4ddu6cctl4zgxhy4wa6cf2z0gpsxw9dkvu.devfee"
)
ALLOWED_METHODS = frozenset(
    ("mining.subscribe", "mining.authorize", "mining.submit")
)


class SoakError(RuntimeError):
    """Raised when a soak safety or qualification contract is violated."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SoakError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_exact_module(path: Path, expected: str, name: str) -> ModuleType:
    require(path.is_file() and path.stat().st_size > 0, f"missing module: {path}")
    actual = sha256_file(path)
    require(
        actual == expected,
        f"{name} checksum mismatch: expected={expected} actual={actual}",
    )
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"cannot import {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def append_jsonl(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
    path.chmod(0o600)


def compact_target(nbits_hex: str) -> int:
    require(
        isinstance(nbits_hex, str) and len(nbits_hex) == 8,
        "nbits is not four-byte hexadecimal",
    )
    try:
        compact = int(nbits_hex, 16)
    except ValueError as exc:
        raise SoakError("nbits is not hexadecimal") from exc
    exponent = compact >> 24
    mantissa = compact & 0x007FFFFF
    require((compact & 0x00800000) == 0, "network target is negative")
    if exponent <= 3:
        target = mantissa >> (8 * (3 - exponent))
    else:
        target = mantissa << (8 * (exponent - 3))
    require(0 < target < (1 << 256), "network target is outside uint256")
    return target


def send_bytes(sock: socket.socket, payload: bytes, deadline: float) -> None:
    view = memoryview(payload)
    while view:
        remaining = deadline - time.monotonic()
        require(remaining > 0, "socket send timed out")
        _, writable, _ = select.select([], [sock], [], min(remaining, 1.0))
        require(bool(writable), "socket did not become writable")
        try:
            count = sock.send(view)
        except BlockingIOError:
            continue
        require(count > 0, "socket closed during send")
        view = view[count:]


class StratumSession:
    def __init__(self, sock: socket.socket, maximum_submits: int) -> None:
        self.sock = sock
        self.maximum_submits = maximum_submits
        self.buffer = bytearray()
        self.events: list[dict[str, Any]] = []
        self.outbound_counts: collections.Counter[str] = collections.Counter()

    def send(self, message: dict[str, Any], timeout: float = 5.0) -> None:
        method = message.get("method")
        require(method in ALLOWED_METHODS, f"forbidden outbound method: {method}")
        if method == "mining.submit":
            require(
                self.outbound_counts[method] < self.maximum_submits,
                "maximum submission safety bound reached",
            )
        payload = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        send_bytes(self.sock, payload, time.monotonic() + timeout)
        self.outbound_counts[method] += 1
        self.events.append({"direction": "outbound", "message": message})

    def receive_available(self) -> tuple[list[dict[str, Any]], bool]:
        closed = False
        while True:
            try:
                chunk = self.sock.recv(65536)
            except BlockingIOError:
                break
            if not chunk:
                closed = True
                break
            self.buffer.extend(chunk)
            require(len(self.buffer) <= 4 * 1024 * 1024, "Stratum buffer exceeded 4 MiB")

        messages: list[dict[str, Any]] = []
        while b"\n" in self.buffer:
            raw, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            if not raw.strip():
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SoakError("DATUM sent malformed JSON") from exc
            require(isinstance(message, dict), "Stratum message is not an object")
            messages.append(message)
            self.events.append({"direction": "inbound", "message": message})
        return messages, closed


class DevFeeScheduler:
    """Select exactly ``slots`` developer sweeps in each fixed-size cycle."""

    def __init__(self, cycle_sweeps: int = 100, slots: int = 1) -> None:
        require(cycle_sweeps >= 100, "developer-fee cycle is below 100 sweeps")
        require(slots == 1, "developer-fee allocation must be exactly one slot")
        self.cycle_sweeps = cycle_sweeps
        self.slots = slots
        self.total_sweeps = 0
        self.developer_sweeps = 0
        self.fallback_sweeps = 0

    def choose(self, developer_ready: bool) -> str:
        slot = self.total_sweeps % self.cycle_sweeps
        self.total_sweeps += 1
        if slot < self.slots:
            if developer_ready:
                self.developer_sweeps += 1
                return "developer"
            self.fallback_sweeps += 1
        return "user"

    def snapshot(self) -> dict[str, int | float]:
        actual = (
            100.0 * self.developer_sweeps / self.total_sweeps
            if self.total_sweeps else 0.0
        )
        return {
            "cycle_sweeps": self.cycle_sweeps,
            "developer_slots": self.slots,
            "total_sweeps": self.total_sweeps,
            "developer_sweeps": self.developer_sweeps,
            "fallback_sweeps": self.fallback_sweeps,
            "actual_percent": round(actual, 6),
        }


class AuxiliaryStratum:
    """Fail-open second Stratum session used only for developer-fee work."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        translator: ModuleType,
        maximum_submits: int,
        handshake_timeout: float,
        reconnect_seconds: float,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.translator = translator
        self.maximum_submits = maximum_submits
        self.handshake_timeout = handshake_timeout
        self.reconnect_seconds = reconnect_seconds
        self.sock: socket.socket | None = None
        self.session: StratumSession | None = None
        self.state: dict[str, Any] = {
            "subscribe": None,
            "difficulty": None,
            "notify": None,
            "authorized": False,
        }
        self.next_connect_at = 0.0
        self.connect_failures = 0
        self.disconnects = 0

    @property
    def ready(self) -> bool:
        return (
            self.sock is not None
            and self.session is not None
            and self.state["subscribe"] is not None
            and self.state["difficulty"] is not None
            and self.state["notify"] is not None
            and self.state["authorized"] is True
        )

    def close(self, disconnected: bool = False) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        if disconnected:
            self.disconnects += 1
        self.sock = None
        self.session = None
        self.state = {
            "subscribe": None,
            "difficulty": None,
            "notify": None,
            "authorized": False,
        }
        self.next_connect_at = time.monotonic() + self.reconnect_seconds

    def _control(self, message: dict[str, Any]) -> bool:
        if message.get("id") == DEVFEE_SUBSCRIBE_ID:
            self.translator.parse_subscribe(message)
            self.state["subscribe"] = message
            return True
        if message.get("id") == DEVFEE_AUTHORIZE_ID:
            if message.get("error") in (None, False) and message.get("result") is True:
                self.state["authorized"] = True
            return True
        if message.get("method") == "mining.set_difficulty":
            params = message.get("params")
            if isinstance(params, list) and len(params) == 1:
                difficulty = self.translator.parse_difficulty(params[0])
                if difficulty >= 1:
                    self.state["difficulty"] = difficulty
            return True
        if message.get("method") == "mining.notify":
            self.translator.parse_notify(message)
            self.state["notify"] = message
            return True
        return False

    def maybe_connect(self) -> bool:
        if self.ready:
            return True
        if time.monotonic() < self.next_connect_at:
            return False
        self.close()
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=self.handshake_timeout
            )
            sock.setblocking(False)
            session = StratumSession(sock, self.maximum_submits)
            self.sock = sock
            self.session = session
            session.send({
                "id": DEVFEE_SUBSCRIBE_ID,
                "method": "mining.subscribe",
                "params": ["fk33-jcm33-bitstream-devfee/1.0"],
            })
            session.send({
                "id": DEVFEE_AUTHORIZE_ID,
                "method": "mining.authorize",
                "params": [self.username, "x"],
            })
            deadline = time.monotonic() + self.handshake_timeout
            while time.monotonic() < deadline and not self.ready:
                readable, _, _ = select.select([sock], [], [], 0.25)
                if not readable:
                    continue
                messages, closed = session.receive_available()
                if closed:
                    raise OSError("developer-fee Stratum closed during handshake")
                for message in messages:
                    self._control(message)
            if not self.ready:
                raise OSError("developer-fee Stratum handshake timed out")
            print(
                f"DEVFEE_READY pool={self.host}:{self.port} "
                f"username={self.username} allocation=1.00%",
                flush=True,
            )
            return True
        except (OSError, SoakError, ValueError, json.JSONDecodeError) as exc:
            self.connect_failures += 1
            self.close()
            print(f"DEVFEE_FALLBACK reason={exc} user_mining=continuing", flush=True)
            return False

    def receive_available(self) -> list[dict[str, Any]]:
        if self.sock is None or self.session is None:
            return []
        try:
            messages, closed = self.session.receive_available()
            if closed:
                self.close(disconnected=True)
                print("DEVFEE_FALLBACK reason=connection-closed user_mining=continuing", flush=True)
                return []
            application: list[dict[str, Any]] = []
            for message in messages:
                if not self._control(message):
                    application.append(message)
            return application
        except (OSError, SoakError, ValueError, json.JSONDecodeError) as exc:
            self.close(disconnected=True)
            print(f"DEVFEE_FALLBACK reason={exc} user_mining=continuing", flush=True)
            return []


class HardwareFrames:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.ignored = 0

    def receive_available(self, sock: socket.socket) -> tuple[list[bytes], bool]:
        closed = False
        while True:
            try:
                chunk = sock.recv(65536)
            except BlockingIOError:
                break
            if not chunk:
                closed = True
                break
            self.buffer.extend(chunk)
            require(len(self.buffer) <= 4 * 1024 * 1024, "hardware buffer exceeded 4 MiB")

        frames: list[bytes] = []
        while True:
            index = self.buffer.find(b"FJ")
            if index < 0:
                if self.buffer[-1:] == b"F":
                    del self.buffer[:-1]
                else:
                    self.buffer.clear()
                break
            if index:
                del self.buffer[:index]
                self.ignored += 1
            if len(self.buffer) < 6:
                break
            payload_length = int.from_bytes(self.buffer[4:6], "little")
            if self.buffer[2] != 1 or payload_length > 4096:
                del self.buffer[0]
                self.ignored += 1
                continue
            total = 6 + payload_length + 2
            if len(self.buffer) < total:
                break
            frames.append(bytes(self.buffer[:total]))
            del self.buffer[:total]
        return frames, closed


def validate_translation(translation: dict[str, Any]) -> None:
    require(
        translation.get("schema") == "fk33-blake2b-live-translation-v1",
        "translation schema mismatch",
    )
    require(translation.get("dialect") == "SIA_SV1", "dialect is not Sia-Sv1")
    difficulty = translation.get("difficulty")
    require(
        not isinstance(difficulty, bool)
        and isinstance(difficulty, (int, float))
        and difficulty > 0,
        "difficulty is invalid",
    )
    difficulty_fraction = Fraction(str(difficulty))
    expected_target = (
        DIFF1_TARGET * difficulty_fraction.denominator
        // difficulty_fraction.numerator
    )
    require(
        translation.get("target_numeric") == f"{expected_target:064x}",
        "translation target does not match difficulty",
    )
    require(translation.get("job_frame_bytes") == 121, "job frame is not 121 bytes")
    require(translation.get("share_submitted") is False, "job was already submitted")
    frame = bytes.fromhex(translation["job_frame"])
    require(
        hashlib.sha256(frame).hexdigest() == translation["job_frame_sha256"],
        "job frame checksum mismatch",
    )


def make_submit_request(
    translation: dict[str, Any], username: str, candidate: bytes, request_id: int
) -> dict[str, Any]:
    require(len(candidate) == 80, "candidate is not ASIC80")

    # PyBLOCK represents the 32-bit nonce as eight hexadecimal digits.
    # ASIC80 stores that nonce little-endian in bytes 32 through 35.
    nonce = int.from_bytes(candidate[32:36], "little")
    nonce_hex = f"{nonce:08x}"

    version = translation.get("version")
    require(
        isinstance(version, str) and len(version) == 8,
        "PyBLOCK submit version is not four-byte hexadecimal",
    )
    bytes.fromhex(version)

    request = {
        "id": request_id,
        "method": "mining.submit",
        "params": [
            username,
            translation["job_id"],
            translation["extranonce2"],
            translation["ntime8"],
            nonce_hex,
            version,
        ],
    }

    preview = translation.get("submit_preview")
    require(isinstance(preview, dict), "translator submit preview is missing")
    preview_params = preview.get("params")
    require(
        isinstance(preview_params, list) and len(preview_params) == 5,
        "translator submit preview is not five-field",
    )
    require(request["params"][:4] == preview_params[:4], "submit prefix changed")
    require(len(request["params"]) == 6, "PyBLOCK submit is not six-field")
    return request


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def run_selftest(adapter: ModuleType, translator: ModuleType) -> None:
    subscribe, _, notify = translator.fixture_messages()
    rolled_extranonce2 = (1).to_bytes(8, "little")
    translation = translator.translate_job(
        adapter,
        subscribe,
        1,
        notify,
        "fk33-sixlane195-benchmark30-v1",
        extranonce2=rolled_extranonce2,
    )
    validate_translation(translation)
    asic80 = bytes.fromhex(translation["asic80"])
    tag = 0x42
    frame = adapter.encode_job(tag, asic80, DIFF1_TARGET)
    decoded = adapter.decode_job(frame)
    require(decoded["tag"] == tag, "self-test transport tag changed")
    require(decoded["asic80"] == asic80, "self-test ASIC80 changed")
    candidate = adapter.apply_returned_nonce(asic80, 0x0BADF00D)
    request = make_submit_request(translation, "fk33-sixlane195-benchmark30-v1", candidate, 1000)
    require(request["params"][2] == rolled_extranonce2.hex(), "rolled extranonce2 changed")
    require(len(request["params"]) == 6, "PyBLOCK submit field count changed")
    require(request["params"][4] == "0badf00d", "PyBLOCK nonce encoding changed")
    require(request["params"][5] == translation["version"], "PyBLOCK version changed")
    estimated_ghs = 420 * EXPECTED_HASHES_PER_DIFF1 / 1800 / 1e9
    require(0.9 < estimated_ghs < 1.1, "hashrate estimator fixture failed")
    scheduler = DevFeeScheduler()
    roles = [scheduler.choose(True) for _ in range(10000)]
    require(roles.count("developer") == 100, "developer fee is not exactly 1%")
    require(roles[0] == "developer", "developer slot is not deterministic")
    fallback = DevFeeScheduler()
    fallback_roles = [fallback.choose(False) for _ in range(100)]
    require(set(fallback_roles) == {"user"}, "developer outage did not fail open")
    require(fallback.snapshot()["fallback_sweeps"] == 1, "fallback was not counted")
    print("RESULT: FK33_BLAKE2B_SIXLANE195_BENCHMARK30_CLIENT_SELFTEST_PASS")


def run_live(args: argparse.Namespace, adapter: ModuleType, translator: ModuleType) -> None:
    username = args.username
    args.shares_output.write_text("")
    args.shares_output.chmod(0o600)

    subscribe_request = {
        "id": SUBSCRIBE_ID,
        "method": "mining.subscribe",
        "params": ["fk33-sixlane195-f2pool-production/3.0"],
    }
    authorize_request = {
        "id": AUTHORIZE_ID,
        "method": "mining.authorize",
        "params": [username, "x"],
    }

    process_started = time.monotonic()
    subscribe: dict[str, Any] | None = None
    announced_difficulty: int | float | None = None
    notify: dict[str, Any] | None = None
    authorized = False
    notify_epoch = 0
    notify_count = 0
    difficulty_history: list[dict[str, Any]] = []

    jobs_dispatched = 0
    timed_rolls = 0
    post_share_advances = 0
    notify_restarts = 0
    tag_counter = 0
    extranonce2_counter = 0
    current_work: dict[str, Any] | None = None
    next_roll_at = 0.0

    accepted = 0
    rejected = 0
    devfee_accepted = 0
    devfee_rejected = 0
    accepted_diff_units = 0
    network_target_shares = 0
    stale_returns = 0
    invalid_frames = 0
    shares_while_response_pending = 0
    pending: dict[int, dict[str, Any]] = {}
    next_submit_id = FIRST_SUBMIT_ID
    accepted_times: list[float] = []
    response_latencies: list[float] = []
    scheduler = DevFeeScheduler(args.devfee_cycle_sweeps, 1)
    devfee: AuxiliaryStratum | None = None

    def process_control_message(message: dict[str, Any]) -> bool:
        nonlocal subscribe, announced_difficulty, notify, authorized
        nonlocal notify_epoch, notify_count
        if message.get("id") == SUBSCRIBE_ID:
            translator.parse_subscribe(message)
            subscribe = message
        elif message.get("id") == AUTHORIZE_ID:
            require(message.get("error") in (None, False), "authorization error")
            authorized = message.get("result") is True
            require(authorized, "DATUM rejected mining.authorize")
        elif message.get("method") == "mining.set_difficulty":
            params = message.get("params")
            require(
                isinstance(params, list) and len(params) == 1,
                "invalid mining.set_difficulty",
            )
            announced_difficulty = translator.parse_difficulty(params[0])
            require(announced_difficulty >= 1, "DATUM difficulty fell below one")
            difficulty_history.append(
                {
                    "elapsed_seconds": round(time.monotonic() - process_started, 6),
                    "difficulty": announced_difficulty,
                }
            )
        elif message.get("method") == "mining.notify":
            translator.parse_notify(message)
            notify = message
            notify_epoch += 1
            notify_count += 1
            return True
        return False

    def process_submit_response(message: dict[str, Any]) -> bool:
        nonlocal accepted, rejected, accepted_diff_units, network_target_shares
        nonlocal devfee_accepted, devfee_rejected
        message_id = message.get("id")
        if not isinstance(message_id, int) or message_id not in pending:
            return False
        share_record = pending.pop(message_id)
        share_record["submit_response"] = message
        share_record["response_latency_seconds"] = round(
            time.monotonic() - float(share_record["submitted_at"]), 6
        )
        role = share_record["mining_role"]
        if message.get("error") not in (None, False) or message.get("result") is not True:
            if role == "developer":
                devfee_rejected += 1
            else:
                rejected += 1
            share_record["accepted"] = False
            append_jsonl(args.shares_output, share_record)
            print(
                f"REJECTED: role={role} submit id {message_id}: "
                f"{message.get('error')} (continuing)",
                flush=True,
            )
            return True
        if role == "developer":
            devfee_accepted += 1
        else:
            accepted += 1
        share_record["accepted"] = True
        if role == "user":
            accepted_diff_units += float(share_record["difficulty"])
            if bool(share_record["network_target_meets"]):
                network_target_shares += 1
        accepted_at = time.monotonic() - soak_started
        accepted_times.append(accepted_at)
        response_latencies.append(float(share_record["response_latency_seconds"]))
        share_record["accepted_elapsed_seconds"] = round(accepted_at, 6)
        append_jsonl(args.shares_output, share_record)
        print(f"ACCEPTED: role={role} submit id {message_id}", flush=True)
        return True

    with socket.create_connection(
        (args.stratum_host, args.stratum_port), timeout=10.0
    ) as stratum_sock:
        stratum_sock.setblocking(False)
        session = StratumSession(stratum_sock, args.maximum_submits)
        session.send(subscribe_request)
        session.send(authorize_request)

        handshake_deadline = time.monotonic() + args.handshake_timeout
        while time.monotonic() < handshake_deadline:
            readable, _, _ = select.select([stratum_sock], [], [], 1.0)
            if not readable:
                continue
            messages, closed = session.receive_available()
            require(not closed, "DATUM closed during handshake")
            for message in messages:
                process_control_message(message)
            if (
                subscribe is not None
                and announced_difficulty is not None
                and notify is not None
                and authorized
            ):
                break

        require(subscribe is not None, "mining.subscribe response timed out")
        require(authorized, "mining.authorize response timed out")
        require(announced_difficulty is not None, "difficulty announcement timed out")
        require(notify is not None, "mining.notify timed out")

        devfee = AuxiliaryStratum(
            args.devfee_host,
            args.devfee_port,
            args.devfee_username,
            translator,
            args.maximum_submits,
            args.devfee_handshake_timeout,
            args.devfee_reconnect_seconds,
        )
        devfee.maybe_connect()

        with socket.create_connection(
            (args.fpga_host, args.fpga_port), timeout=10.0
        ) as fpga_sock:
            fpga_sock.setblocking(False)
            hardware = HardwareFrames()

            def dispatch_work(reason: str) -> None:
                nonlocal jobs_dispatched, timed_rolls, post_share_advances
                nonlocal notify_restarts, tag_counter, extranonce2_counter
                nonlocal current_work, next_roll_at
                role = scheduler.choose(devfee is not None and devfee.ready)
                if role == "developer":
                    work_subscribe = devfee.state["subscribe"]
                    work_notify = devfee.state["notify"]
                    work_difficulty = devfee.state["difficulty"]
                    work_username = devfee.username
                else:
                    work_subscribe = subscribe
                    work_notify = notify
                    work_difficulty = announced_difficulty
                    work_username = username
                require(work_subscribe is not None, "work dispatch lacks subscribe state")
                require(work_notify is not None, "work dispatch lacks notify state")
                require(work_difficulty is not None, "work dispatch lacks difficulty")

                _, extranonce2_size = translator.parse_subscribe(work_subscribe)
                require(
                    isinstance(extranonce2_size, int)
                    and 1 <= extranonce2_size <= 16,
                    "invalid negotiated extranonce2 size",
                )
                extranonce2_modulus = 1 << (8 * extranonce2_size)
                extranonce2 = (
                    extranonce2_counter % extranonce2_modulus
                ).to_bytes(extranonce2_size, "little")
                extranonce2_counter += 1
                translation = translator.translate_job(
                    adapter,
                    work_subscribe,
                    work_difficulty,
                    work_notify,
                    work_username,
                    extranonce2=extranonce2,
                )
                validate_translation(translation)
                tag = tag_counter & 0xFF
                tag_counter += 1
                asic80 = bytes.fromhex(translation["asic80"])
                target = int(translation["target_numeric"], 16)
                frame = adapter.encode_job(tag, asic80, target)
                decoded = adapter.decode_job(frame)
                require(decoded["tag"] == tag, "dispatched tag changed")
                require(decoded["asic80"] == asic80, "dispatched ASIC80 changed")
                require(decoded["target_numeric"] == target, "dispatched target changed")

                translation = dict(translation)
                translation["tag"] = f"{tag:02x}"
                translation["job_frame"] = frame.hex()
                translation["job_frame_sha256"] = hashlib.sha256(frame).hexdigest()
                send_bytes(fpga_sock, frame, time.monotonic() + 5.0)
                jobs_dispatched += 1
                if reason == "nonce-space-roll":
                    timed_rolls += 1
                elif reason == "post-share":
                    post_share_advances += 1
                elif reason == "notify":
                    notify_restarts += 1
                current_work = {
                    "translation": translation,
                    "tag": tag,
                    "epoch": notify_epoch,
                    "reason": reason,
                    "dispatch_index": jobs_dispatched,
                    "dispatched_at": time.monotonic(),
                    "mining_role": role,
                    "submit_username": work_username,
                }
                next_roll_at = time.monotonic() + args.roll_seconds

            dispatch_work("initial")
            soak_started = time.monotonic()
            soak_deadline = soak_started + args.duration_seconds
            first_share_deadline = soak_started + args.first_share_timeout
            next_progress_at = soak_started + args.progress_seconds

            while time.monotonic() < soak_deadline:
                if devfee is not None and not devfee.ready:
                    devfee.maybe_connect()
                now = time.monotonic()
                timeout = min(0.25, max(0.0, soak_deadline - now))
                read_sockets = [stratum_sock, fpga_sock]
                if devfee is not None and devfee.sock is not None:
                    read_sockets.append(devfee.sock)
                readable, _, _ = select.select(
                    read_sockets, [], [], timeout
                )

                if stratum_sock in readable:
                    messages, closed = session.receive_available()
                    require(not closed, "DATUM closed during soak")
                    notify_changed = False
                    for message in messages:
                        if process_submit_response(message):
                            continue
                        if process_control_message(message):
                            notify_changed = True
                    if notify_changed:
                        dispatch_work("notify")

                if (
                    devfee is not None
                    and devfee.sock is not None
                    and devfee.sock in readable
                ):
                    for message in devfee.receive_available():
                        process_submit_response(message)

                if fpga_sock in readable:
                    frames, closed = hardware.receive_available(fpga_sock)
                    require(not closed, "FK33 transport closed during soak")
                    for frame in frames:
                        try:
                            share = adapter.decode_share(frame)
                        except adapter.ProtocolError:
                            invalid_frames += 1
                            continue
                        require(current_work is not None, "share arrived without active work")
                        if int(share["tag"]) != int(current_work["tag"]):
                            stale_returns += 1
                            continue
                        role = current_work["mining_role"]
                        if any(
                            record["mining_role"] == role
                            for record in pending.values()
                        ):
                            shares_while_response_pending += 1
                            continue

                        translation = dict(current_work["translation"])
                        asic80 = bytes.fromhex(translation["asic80"])
                        nonce = int(share["nonce"])
                        candidate = adapter.apply_returned_nonce(asic80, nonce)
                        raw_digest, final_digest = adapter.hash_candidate(candidate, bytes(32))
                        target = int(translation["target_numeric"], 16)
                        require(
                            bytes(share["digest_wire"]) == bytes(32),
                            "lean share digest field is not zero",
                        )
                        require(
                            adapter.meets_target(final_digest, target),
                            "FPGA returned a host-invalid share",
                        )
                        network_target = compact_target(translation["nbits"])
                        if role == "developer":
                            request_id = DEVFEE_FIRST_SUBMIT_ID + next_submit_id
                        else:
                            request_id = next_submit_id
                        next_submit_id += 1
                        request = make_submit_request(
                            translation, current_work["submit_username"], candidate, request_id
                        )
                        if role == "developer":
                            if devfee is None or not devfee.ready or devfee.session is None:
                                stale_returns += 1
                                continue
                            devfee.session.send(request)
                        else:
                            session.send(request)
                        pending[request_id] = {
                            "schema": "fk33-blake2b-sixlane195-benchmark30-share-v1",
                            "submit_id": request_id,
                            "job_id": translation["job_id"],
                            "difficulty": translation["difficulty"],
                            "tag": translation["tag"],
                            "dispatch_index": current_work["dispatch_index"],
                            "dispatch_reason": current_work["reason"],
                            "extranonce2": translation["extranonce2"],
                            "returned_nonce": f"{nonce:08x}",
                            "nonce_submit": request["params"][4],
                            "raw_digest": raw_digest.hex(),
                            "final_digest": final_digest.hex(),
                            "share_target_numeric": translation["target_numeric"],
                            "network_target_numeric": f"{network_target:064x}",
                            "share_target_meets": True,
                            "network_target_meets": adapter.meets_target(
                                final_digest, network_target
                            ),
                            "job_frame_sha256": translation["job_frame_sha256"],
                            "share_frame_sha256": hashlib.sha256(frame).hexdigest(),
                            "submit_request": request,
                            "submitted_at": time.monotonic(),
                            "mining_role": role,
                        }
                        dispatch_work("post-share")

                now = time.monotonic()
                expired = [
                    request_id
                    for request_id, record in pending.items()
                    if now - float(record["submitted_at"]) > args.response_timeout
                ]
                for request_id in expired:
                    record = pending.pop(request_id)
                    role = record["mining_role"]
                    record["accepted"] = False
                    record["submit_response"] = {"error": "response-timeout"}
                    if role == "developer":
                        devfee_rejected += 1
                    else:
                        rejected += 1
                    append_jsonl(args.shares_output, record)
                    print(
                        f"REJECTED: role={role} submit id {request_id}: "
                        "response-timeout (continuing)",
                        flush=True,
                    )
                if accepted == 0 and now >= first_share_deadline:
                    raise SoakError("first accepted share gate timed out")
                if now >= next_roll_at:
                    dispatch_work("nonce-space-roll")
                if now >= next_progress_at:
                    elapsed = now - soak_started
                    estimated_ghs = (
                        accepted_diff_units * EXPECTED_HASHES_PER_DIFF1 / elapsed / 1e9
                        if elapsed > 0 else 0.0
                    )
                    print(
                        "PROGRESS: "
                        f"elapsed={elapsed:.1f}s accepted={accepted} rejected={rejected} "
                        f"devfee_accepted={devfee_accepted} "
                        f"devfee_rejected={devfee_rejected} "
                        f"diff_units={accepted_diff_units} estimated={estimated_ghs:.3f}GH/s "
                        f"current_diff={announced_difficulty} jobs={jobs_dispatched} "
                        f"timed_rolls={timed_rolls} devfee={scheduler.snapshot()}",
                        flush=True,
                    )
                    next_progress_at += args.progress_seconds

            response_deadline = time.monotonic() + args.response_timeout
            while pending and time.monotonic() < response_deadline:
                response_sockets = [stratum_sock]
                if devfee is not None and devfee.sock is not None:
                    response_sockets.append(devfee.sock)
                readable, _, _ = select.select(response_sockets, [], [], 0.5)
                if not readable:
                    continue
                if stratum_sock in readable:
                    messages, closed = session.receive_available()
                    require(not closed, "DATUM closed with a submit response pending")
                    for message in messages:
                        process_submit_response(message)
                if (
                    devfee is not None
                    and devfee.sock is not None
                    and devfee.sock in readable
                ):
                    for message in devfee.receive_available():
                        process_submit_response(message)

            for request_id in list(pending):
                record = pending[request_id]
                if record["mining_role"] != "developer":
                    continue
                pending.pop(request_id)
                devfee_rejected += 1
                record["accepted"] = False
                record["submit_response"] = {"error": "developer-response-timeout"}
                append_jsonl(args.shares_output, record)
            require(not pending, "user submit response remained pending after soak")
            soak_elapsed = time.monotonic() - soak_started

        if devfee is not None:
            devfee.close()

    transcript = {
        "schema": "fk33-blake2b-sixlane195-benchmark30-transcript-v1",
        "events": session.events,
        "outbound_method_counts": dict(session.outbound_counts),
    }
    write_json(args.messages_output, transcript)

    intervals = [
        accepted_times[index] - accepted_times[index - 1]
        for index in range(1, len(accepted_times))
    ]
    estimated_ghs = (
        accepted_diff_units * EXPECTED_HASHES_PER_DIFF1 / soak_elapsed / 1e9
        if soak_elapsed > 0 else 0.0
    )
    submissions = session.outbound_counts["mining.submit"]
    checks = {
        "duration_reached": soak_elapsed >= args.duration_seconds,
        "first_share_accepted": accepted >= 1,
        "minimum_accepted_shares": accepted >= args.minimum_accepted_shares,
        "minimum_accepted_diff_units": accepted_diff_units >= args.minimum_diff_units,
        "zero_rejections": rejected == 0,
        "all_submissions_answered": submissions == accepted + rejected,
        "single_subscribe": session.outbound_counts["mining.subscribe"] == 1,
        "single_authorize": session.outbound_counts["mining.authorize"] == 1,
        "multiple_jobs_dispatched": jobs_dispatched > 1,
        "no_invalid_hardware_frames": invalid_frames == 0,
        "hashrate_lower_gate": estimated_ghs >= args.minimum_ghs,
        "hashrate_upper_gate": estimated_ghs <= args.maximum_ghs,
    }
    record = {
        "schema": "fk33-blake2b-sixlane195-benchmark30-v1",
        "result": "PASS" if all(checks.values()) else "FAIL",
        "duration_target_seconds": args.duration_seconds,
        "duration_actual_seconds": round(soak_elapsed, 6),
        "username": username,
        "accepted_shares": accepted,
        "rejected_shares": rejected,
        "developer_accepted_shares": devfee_accepted,
        "developer_rejected_shares": devfee_rejected,
        "submission_count": submissions,
        "acceptance_ratio": accepted / submissions if submissions else 0.0,
        "accepted_difficulty_units": accepted_diff_units,
        "estimated_hashrate_ghs": round(estimated_ghs, 6),
        "hashrate_gate_ghs": [args.minimum_ghs, args.maximum_ghs],
        "network_target_shares": network_target_shares,
        "jobs_dispatched": jobs_dispatched,
        "timed_extranonce2_rolls": timed_rolls,
        "post_share_work_advances": post_share_advances,
        "notify_restarts": notify_restarts,
        "notify_count": notify_count,
        "difficulty_history": difficulty_history,
        "stale_hardware_returns": stale_returns,
        "invalid_hardware_frames": invalid_frames,
        "ignored_transport_bytes": hardware.ignored,
        "shares_while_response_pending": shares_while_response_pending,
        "developer_fee": scheduler.snapshot(),
        "first_accepted_seconds": round(accepted_times[0], 6) if accepted_times else None,
        "last_accepted_seconds": round(accepted_times[-1], 6) if accepted_times else None,
        "share_interval_seconds": {
            "count": len(intervals),
            "minimum": round(min(intervals), 6) if intervals else None,
            "median": round(statistics.median(intervals), 6) if intervals else None,
            "p95": round(percentile(intervals, 0.95), 6) if intervals else None,
            "maximum": round(max(intervals), 6) if intervals else None,
        },
        "submit_response_latency_seconds": {
            "count": len(response_latencies),
            "median": round(statistics.median(response_latencies), 6)
            if response_latencies else None,
            "p95": round(percentile(response_latencies, 0.95), 6)
            if response_latencies else None,
            "maximum": round(max(response_latencies), 6)
            if response_latencies else None,
        },
        "submission_transmitted": submissions > 0,
        "checks": checks,
    }
    write_json(args.output, record)
    print(json.dumps(record, indent=2, sort_keys=True))
    require(all(checks.values()), "30-minute soak result failed one or more gates")
    print("RESULT: FK33_BLAKE2B_SIXLANE195_BENCHMARK30_PASS")
    print(f"PASS: DATUM accepted all {accepted} submitted physical-FPGA shares")
    print(f"PASS: measured share-difficulty hashrate was {estimated_ghs:.3f} GH/s")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--translator", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("selftest")
    live = subparsers.add_parser("run")
    live.add_argument("--stratum-host", default="127.0.0.1")
    live.add_argument("--stratum-port", type=int, default=21020)
    live.add_argument("--fpga-host", default="127.0.0.1")
    live.add_argument("--fpga-port", type=int, default=23000)
    live.add_argument("--username", default="fk33-sixlane195-benchmark30-v1")
    live.add_argument("--duration-seconds", type=float, default=1800.0)
    live.add_argument("--roll-seconds", type=float, default=5.0)
    live.add_argument("--handshake-timeout", type=float, default=45.0)
    live.add_argument("--first-share-timeout", type=float, default=180.0)
    live.add_argument("--response-timeout", type=float, default=30.0)
    live.add_argument("--progress-seconds", type=float, default=60.0)
    live.add_argument("--maximum-submits", type=int, default=2000)
    live.add_argument("--devfee-host", default="pool.pyblock.xyz")
    live.add_argument("--devfee-port", type=int, default=21020)
    live.add_argument("--devfee-username", default=DEFAULT_DEVFEE_USERNAME)
    live.add_argument("--devfee-cycle-sweeps", type=int, default=100)
    live.add_argument("--devfee-handshake-timeout", type=float, default=3.0)
    live.add_argument("--devfee-reconnect-seconds", type=float, default=60.0)
    live.add_argument("--minimum-accepted-shares", type=int, default=100)
    live.add_argument("--minimum-diff-units", type=int, default=250)
    live.add_argument("--minimum-ghs", type=float, default=0.70)
    live.add_argument("--maximum-ghs", type=float, default=1.30)
    live.add_argument("--output", required=True, type=Path)
    live.add_argument("--shares-output", required=True, type=Path)
    live.add_argument("--messages-output", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        adapter = load_exact_module(args.adapter, ADAPTER_SHA256, "fk33_adapter")
        translator = load_exact_module(
            args.translator, TRANSLATOR_SHA256, "fk33_translator"
        )
        if args.command == "selftest":
            run_selftest(adapter, translator)
        else:
            require(
                3600.0 <= args.duration_seconds <= 604800.0,
                "production duration must be between one hour and seven days",
            )
            require(
                0.15 <= args.roll_seconds <= 0.50,
                "PyBLOCK roll interval must be between 0.15 and 0.50 seconds",
            )
            require(
                1 <= args.maximum_submits <= 2000,
                "production submission safety bound is invalid",
            )
            require(
                args.devfee_cycle_sweeps == 100,
                "developer fee must be exactly one of every 100 sweeps",
            )
            require(
                args.devfee_username == DEFAULT_DEVFEE_USERNAME,
                "developer-fee username differs from disclosed policy",
            )
            require(
                0.5 <= args.devfee_handshake_timeout <= 10.0,
                "developer-fee handshake timeout is invalid",
            )
            require(
                10.0 <= args.devfee_reconnect_seconds <= 3600.0,
                "developer-fee reconnect interval is invalid",
            )
            run_live(args, adapter, translator)
        return 0
    except (
        SoakError,
        ValueError,
        AssertionError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
