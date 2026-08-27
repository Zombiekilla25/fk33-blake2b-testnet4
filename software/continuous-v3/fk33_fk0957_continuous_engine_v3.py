#!/usr/bin/env python3
"""Run the authenticated FK0957 BLAKE2b miner until cleanly stopped."""

from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import os
import select
import signal
import socket
import statistics
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any


ADAPTER_SHA256 = (
    "5bc0982b893cea747514c9005805df9b0d4f380fb94ca733a3eada378e73a3f7"
)
TRANSLATOR_SHA256 = (
    "f63f8ce38bf2a774646837e66fe029f3b05d52c6a78c11b536485935eee36d1b"
)
DIFF1_TARGET = 0x00000000FFFF0000 << 192
EXPECTED_HASHES_PER_DIFF1 = (1 << 256) / (DIFF1_TARGET + 1)
SUBSCRIBE_ID = 1
AUTHORIZE_ID = 2
FIRST_SUBMIT_ID = 1000
MAX_EVENT_FILE_BYTES = 64 * 1024 * 1024
MAX_EVENT_FILE_BACKUPS = 8
ALLOWED_METHODS = frozenset(
    ("mining.subscribe", "mining.authorize", "mining.submit")
)


class MinerError(RuntimeError):
    """Base class for controlled miner failures."""


class RecoverableMinerError(MinerError):
    """A socket/session fault that may be retried inside the recovery window."""


class FatalMinerError(MinerError):
    """A protocol, validation, rejection, or safety fault that must stop mining."""


STOP_REQUESTED = False
STOP_SIGNAL: int | None = None


def request_stop(signum: int, _frame: Any) -> None:
    global STOP_REQUESTED, STOP_SIGNAL
    STOP_REQUESTED = True
    STOP_SIGNAL = signum


def require(condition: bool, message: str) -> None:
    if not condition:
        raise FatalMinerError(message)


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


def write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)


class RotatingJsonl:
    def __init__(
        self,
        path: Path,
        maximum_bytes: int = MAX_EVENT_FILE_BYTES,
        backups: int = MAX_EVENT_FILE_BACKUPS,
    ) -> None:
        self.path = path
        self.maximum_bytes = maximum_bytes
        self.backups = backups
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def rotate_if_needed(self, incoming: int) -> None:
        current = self.path.stat().st_size if self.path.exists() else 0
        if current + incoming <= self.maximum_bytes:
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backups}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            destination = self.path.with_name(f"{self.path.name}.{index + 1}")
            if source.exists():
                os.replace(source, destination)
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))

    def append(self, value: Any) -> None:
        payload = (json.dumps(value, sort_keys=True) + "\n").encode()
        self.rotate_if_needed(len(payload))
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def compact_target(nbits_hex: str) -> int:
    require(
        isinstance(nbits_hex, str) and len(nbits_hex) == 8,
        "nbits is not four-byte hexadecimal",
    )
    try:
        compact = int(nbits_hex, 16)
    except ValueError as exc:
        raise FatalMinerError("nbits is not hexadecimal") from exc
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
        if STOP_REQUESTED:
            raise RecoverableMinerError("stop requested during socket send")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RecoverableMinerError("socket send timed out")
        _, writable, _ = select.select([], [sock], [], min(remaining, 1.0))
        if not writable:
            continue
        try:
            count = sock.send(view)
        except (BlockingIOError, InterruptedError):
            continue
        except OSError as exc:
            raise RecoverableMinerError(f"socket send failed: {exc}") from exc
        if count <= 0:
            raise RecoverableMinerError("socket closed during send")
        view = view[count:]


class StratumSession:
    def __init__(
        self,
        sock: socket.socket,
        events: RotatingJsonl,
        session_number: int,
        maximum_submits: int,
    ) -> None:
        self.sock = sock
        self.events = events
        self.session_number = session_number
        self.maximum_submits = maximum_submits
        self.buffer = bytearray()
        self.outbound_counts: collections.Counter[str] = collections.Counter()

    def record(self, direction: str, message: dict[str, Any]) -> None:
        self.events.append(
            {
                "schema": "fk33-blake2b-continuous-stratum-event-v3",
                "timestamp": time.time(),
                "session_number": self.session_number,
                "direction": direction,
                "message": message,
            }
        )

    def send(self, message: dict[str, Any], timeout: float = 5.0) -> None:
        method = message.get("method")
        require(method in ALLOWED_METHODS, f"forbidden outbound method: {method}")
        if method == "mining.submit":
            require(
                self.outbound_counts[method] < self.maximum_submits,
                "per-session submission safety bound reached",
            )
        payload = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        send_bytes(self.sock, payload, time.monotonic() + timeout)
        self.outbound_counts[method] += 1
        self.record("outbound", message)

    def receive_available(self) -> tuple[list[dict[str, Any]], bool]:
        closed = False
        while True:
            try:
                chunk = self.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError as exc:
                raise RecoverableMinerError(f"Stratum receive failed: {exc}") from exc
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
                raise FatalMinerError("DATUM sent malformed JSON") from exc
            require(isinstance(message, dict), "Stratum message is not an object")
            messages.append(message)
            self.record("inbound", message)
        return messages, closed


class HardwareFrames:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.ignored_bytes = 0

    def receive_available(self, sock: socket.socket) -> tuple[list[bytes], bool]:
        closed = False
        while True:
            try:
                chunk = sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError as exc:
                raise RecoverableMinerError(f"hardware receive failed: {exc}") from exc
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
                    self.ignored_bytes += max(0, len(self.buffer) - 1)
                    del self.buffer[:-1]
                else:
                    self.ignored_bytes += len(self.buffer)
                    self.buffer.clear()
                break
            if index:
                self.ignored_bytes += index
                del self.buffer[:index]
            if len(self.buffer) < 6:
                break
            payload_length = int.from_bytes(self.buffer[4:6], "little")
            if self.buffer[2] != 1 or payload_length > 4096:
                del self.buffer[0]
                self.ignored_bytes += 1
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
    require(isinstance(difficulty, int) and difficulty >= 1, "difficulty is invalid")
    require(
        translation.get("target_numeric") == f"{DIFF1_TARGET // difficulty:064x}",
        "translation target does not match difficulty",
    )
    require(translation.get("job_frame_bytes") == 121, "job frame is not 121 bytes")
    require(translation.get("share_submitted") is False, "job was already submitted")


def make_submit_request(
    translation: dict[str, Any], username: str, candidate: bytes, request_id: int
) -> dict[str, Any]:
    require(len(candidate) == 80, "candidate is not ASIC80")
    request = {
        "id": request_id,
        "method": "mining.submit",
        "params": [
            username,
            translation["job_id"],
            translation["extranonce2"],
            translation["ntime8"],
            candidate[32:40].hex(),
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
    return request


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


class ContinuousMiner:
    def __init__(
        self,
        args: argparse.Namespace,
        adapter: ModuleType,
        translator: ModuleType,
    ) -> None:
        self.args = args
        self.adapter = adapter
        self.translator = translator
        self.started_monotonic = time.monotonic()
        self.started_unix = time.time()
        self.state_path = args.state_output
        self.shares = RotatingJsonl(args.shares_output)
        self.stratum_events = RotatingJsonl(args.stratum_events_output)
        self.session_events = RotatingJsonl(args.session_events_output)

        self.status = "STARTING"
        self.last_error: str | None = None
        self.sessions_started = 0
        self.sessions_completed = 0
        self.reconnects = 0
        self.recovery_failures = 0
        self.accepted = 0
        self.rejected = 0
        self.unknown_submissions = 0
        self.submissions = 0
        self.accepted_diff_units = 0
        self.network_target_shares = 0
        self.jobs_dispatched = 0
        self.timed_rolls = 0
        self.post_share_advances = 0
        self.notify_restarts = 0
        self.notify_count = 0
        self.stale_returns = 0
        self.invalid_frames = 0
        self.shares_while_response_pending = 0
        self.ignored_transport_bytes = 0
        self.next_extranonce2 = 0
        self.next_tag = 0
        self.next_submit_id = FIRST_SUBMIT_ID
        self.current_difficulty: int | None = None
        self.current_job_id: str | None = None
        self.last_accepted_monotonic: float | None = None
        self.last_accepted_unix: float | None = None
        self.first_accepted_seconds: float | None = None
        self.last_progress_at = self.started_monotonic
        self.last_checkpoint_at = 0.0
        self.completed_active_seconds = 0.0
        self.current_session_started: float | None = None
        self.accepted_times: list[float] = []
        self.response_latencies: list[float] = []
        self.accepted_samples: list[tuple[float, int]] = []
        self.difficulty_history: list[dict[str, Any]] = []

    def active_seconds(self) -> float:
        active = self.completed_active_seconds
        if self.current_session_started is not None:
            active += max(0.0, time.monotonic() - self.current_session_started)
        return active

    def estimated_ghs(self) -> float:
        active = self.active_seconds()
        if active <= 0:
            return 0.0
        return self.accepted_diff_units * EXPECTED_HASHES_PER_DIFF1 / active / 1e9

    def rolling_ghs(self) -> float:
        now = time.monotonic()
        cutoff = now - self.args.hashrate_watchdog_seconds
        while self.accepted_samples and self.accepted_samples[0][0] < cutoff:
            del self.accepted_samples[0]
        duration = min(
            now - self.started_monotonic,
            self.args.hashrate_watchdog_seconds,
        )
        if duration <= 0:
            return 0.0
        difficulty_units = sum(value for _, value in self.accepted_samples)
        return difficulty_units * EXPECTED_HASHES_PER_DIFF1 / duration / 1e9

    def trim_statistical_history(self) -> None:
        if len(self.accepted_times) > 100000:
            del self.accepted_times[:10000]
        if len(self.response_latencies) > 100000:
            del self.response_latencies[:10000]
        if len(self.difficulty_history) > 4096:
            del self.difficulty_history[:512]

    def snapshot(self) -> dict[str, Any]:
        now_mono = time.monotonic()
        wall_seconds = now_mono - self.started_monotonic
        intervals = [
            self.accepted_times[index] - self.accepted_times[index - 1]
            for index in range(1, len(self.accepted_times))
        ]
        return {
            "schema": "fk33-blake2b-continuous-state-v3",
            "status": self.status,
            "pid": os.getpid(),
            "started_unix": self.started_unix,
            "updated_unix": time.time(),
            "wall_runtime_seconds": round(wall_seconds, 6),
            "active_mining_seconds": round(self.active_seconds(), 6),
            "last_error": self.last_error,
            "stop_signal": STOP_SIGNAL,
            "sessions_started": self.sessions_started,
            "sessions_completed": self.sessions_completed,
            "reconnects": self.reconnects,
            "recovery_failures": self.recovery_failures,
            "accepted_shares": self.accepted,
            "rejected_shares": self.rejected,
            "unknown_submissions": self.unknown_submissions,
            "submission_count": self.submissions,
            "acceptance_ratio": self.accepted / self.submissions if self.submissions else 0.0,
            "accepted_difficulty_units": self.accepted_diff_units,
            "estimated_hashrate_ghs": round(self.estimated_ghs(), 6),
            "rolling_30m_hashrate_ghs": round(self.rolling_ghs(), 6),
            "network_target_shares": self.network_target_shares,
            "jobs_dispatched": self.jobs_dispatched,
            "timed_extranonce2_rolls": self.timed_rolls,
            "post_share_work_advances": self.post_share_advances,
            "notify_restarts": self.notify_restarts,
            "notify_count": self.notify_count,
            "stale_hardware_returns": self.stale_returns,
            "invalid_hardware_frames": self.invalid_frames,
            "ignored_transport_bytes": self.ignored_transport_bytes,
            "shares_while_response_pending": self.shares_while_response_pending,
            "current_difficulty": self.current_difficulty,
            "current_job_id": self.current_job_id,
            "first_accepted_seconds": self.first_accepted_seconds,
            "last_accepted_unix": self.last_accepted_unix,
            "last_accepted_age_seconds": (
                round(now_mono - self.last_accepted_monotonic, 6)
                if self.last_accepted_monotonic is not None else None
            ),
            "share_interval_seconds": {
                "count": len(intervals),
                "median": round(statistics.median(intervals), 6) if intervals else None,
                "p95": round(percentile(intervals, 0.95), 6) if intervals else None,
                "maximum": round(max(intervals), 6) if intervals else None,
            },
            "submit_response_latency_seconds": {
                "count": len(self.response_latencies),
                "median": round(statistics.median(self.response_latencies), 6)
                if self.response_latencies else None,
                "p95": round(percentile(self.response_latencies, 0.95), 6)
                if self.response_latencies else None,
                "maximum": round(max(self.response_latencies), 6)
                if self.response_latencies else None,
            },
            "difficulty_history": self.difficulty_history[-128:],
            "records": {
                "accepted_shares": str(self.args.shares_output),
                "stratum_events": str(self.args.stratum_events_output),
                "session_events": str(self.args.session_events_output),
            },
        }

    def checkpoint(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self.last_checkpoint_at >= self.args.checkpoint_seconds:
            self.trim_statistical_history()
            write_json_atomic(self.state_path, self.snapshot())
            self.last_checkpoint_at = now

    def progress(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self.last_progress_at >= self.args.progress_seconds:
            print(
                "MINER: "
                f"status={self.status} wall={now - self.started_monotonic:.0f}s "
                f"active={self.active_seconds():.0f}s accepted={self.accepted} "
                f"rejected={self.rejected} unknown={self.unknown_submissions} "
                f"diff_units={self.accepted_diff_units} "
                f"estimated={self.estimated_ghs():.3f}GH/s "
                f"rolling30m={self.rolling_ghs():.3f}GH/s "
                f"sessions={self.sessions_started} reconnects={self.reconnects} "
                f"difficulty={self.current_difficulty} jobs={self.jobs_dispatched}",
                flush=True,
            )
            self.last_progress_at = now

    def record_session(self, event: str, **fields: Any) -> None:
        self.session_events.append(
            {
                "schema": "fk33-blake2b-continuous-session-event-v3",
                "timestamp": time.time(),
                "event": event,
                **fields,
            }
        )

    def connect(self, host: str, port: int, label: str) -> socket.socket:
        try:
            sock = socket.create_connection((host, port), timeout=10.0)
        except OSError as exc:
            raise RecoverableMinerError(f"{label} connection failed: {exc}") from exc
        sock.setblocking(False)
        return sock

    def run_session(self) -> None:
        self.sessions_started += 1
        session_number = self.sessions_started
        self.status = "CONNECTING"
        self.last_error = None
        self.current_difficulty = None
        self.current_job_id = None
        self.checkpoint(force=True)
        self.record_session("session-start", session_number=session_number)

        stratum_sock = self.connect(
            self.args.stratum_host, self.args.stratum_port, "Stratum"
        )
        try:
            fpga_sock = self.connect(self.args.fpga_host, self.args.fpga_port, "FK33")
        except Exception:
            stratum_sock.close()
            raise

        session_started = time.monotonic()
        self.current_session_started = session_started
        session_accepted_start = self.accepted
        session_submissions_start = self.submissions

        try:
            session = StratumSession(
                stratum_sock,
                self.stratum_events,
                session_number,
                self.args.maximum_submits_per_session,
            )
            hardware = HardwareFrames()
            subscribe_request = {
                "id": SUBSCRIBE_ID,
                "method": "mining.subscribe",
                "params": ["fk33-continuous-v3/3.0"],
            }
            authorize_request = {
                "id": AUTHORIZE_ID,
                "method": "mining.authorize",
                "params": [self.args.username, "x"],
            }
            session.send(subscribe_request)
            session.send(authorize_request)

            subscribe: dict[str, Any] | None = None
            pending_difficulty: int | None = None
            active_difficulty: int | None = None
            notify: dict[str, Any] | None = None
            authorized = False
            notify_epoch = 0
            current_work: dict[str, Any] | None = None
            next_roll_at = 0.0
            pending_submit: dict[str, Any] | None = None
            pending_since = 0.0
            last_session_accept = session_started

            def process_control(message: dict[str, Any]) -> bool:
                nonlocal subscribe, pending_difficulty, active_difficulty
                nonlocal notify, authorized, notify_epoch
                if message.get("id") == SUBSCRIBE_ID:
                    self.translator.parse_subscribe(message)
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
                    pending_difficulty = self.translator.parse_difficulty(params[0])
                    require(pending_difficulty >= 1, "DATUM difficulty fell below one")
                elif message.get("method") == "mining.notify":
                    self.translator.parse_notify(message)
                    require(pending_difficulty is not None, "notify arrived before difficulty")
                    active_difficulty = pending_difficulty
                    self.current_difficulty = active_difficulty
                    self.difficulty_history.append(
                        {
                            "timestamp": time.time(),
                            "session_number": session_number,
                            "difficulty": active_difficulty,
                        }
                    )
                    notify = message
                    notify_epoch += 1
                    self.notify_count += 1
                    return True
                return False

            handshake_deadline = time.monotonic() + self.args.handshake_timeout
            while not STOP_REQUESTED and time.monotonic() < handshake_deadline:
                readable, _, _ = select.select([stratum_sock], [], [], 1.0)
                if not readable:
                    continue
                messages, closed = session.receive_available()
                for message in messages:
                    process_control(message)
                if (
                    subscribe is not None
                    and active_difficulty is not None
                    and notify is not None
                    and authorized
                ):
                    break
                if closed:
                    raise RecoverableMinerError("DATUM closed during handshake")

            if STOP_REQUESTED:
                return
            if subscribe is None or not authorized or active_difficulty is None or notify is None:
                raise RecoverableMinerError("Stratum handshake timed out")

            def dispatch_work(reason: str) -> None:
                nonlocal current_work, next_roll_at
                require(subscribe is not None, "work dispatch lacks subscribe state")
                require(notify is not None, "work dispatch lacks notify state")
                require(active_difficulty is not None, "work dispatch lacks difficulty")
                require(self.next_extranonce2 < (1 << 64), "extranonce2 space exhausted")

                extranonce2 = self.next_extranonce2.to_bytes(8, "little")
                self.next_extranonce2 += 1
                translation = self.translator.translate_job(
                    self.adapter,
                    subscribe,
                    active_difficulty,
                    notify,
                    self.args.username,
                    extranonce2=extranonce2,
                )
                validate_translation(translation)
                tag = self.next_tag & 0xFF
                self.next_tag += 1
                asic80 = bytes.fromhex(translation["asic80"])
                target = int(translation["target_numeric"], 16)
                frame = self.adapter.encode_job(tag, asic80, target)
                decoded = self.adapter.decode_job(frame)
                require(decoded["tag"] == tag, "dispatched tag changed")
                require(decoded["asic80"] == asic80, "dispatched ASIC80 changed")
                require(decoded["target_numeric"] == target, "dispatched target changed")
                send_bytes(fpga_sock, frame, time.monotonic() + 5.0)

                translation = dict(translation)
                translation["tag"] = f"{tag:02x}"
                translation["job_frame"] = frame.hex()
                translation["job_frame_sha256"] = hashlib.sha256(frame).hexdigest()
                self.jobs_dispatched += 1
                if reason == "nonce-space-roll":
                    self.timed_rolls += 1
                elif reason == "post-share":
                    self.post_share_advances += 1
                elif reason == "notify":
                    self.notify_restarts += 1
                current_work = {
                    "translation": translation,
                    "tag": tag,
                    "epoch": notify_epoch,
                    "reason": reason,
                    "dispatch_index": self.jobs_dispatched,
                    "dispatched_at": time.monotonic(),
                }
                self.current_job_id = translation["job_id"]
                next_roll_at = time.monotonic() + self.args.roll_seconds

            dispatch_work("initial")
            self.status = "MINING"
            self.checkpoint(force=True)
            self.record_session(
                "session-ready",
                session_number=session_number,
                difficulty=active_difficulty,
                job_id=self.current_job_id,
            )

            while not STOP_REQUESTED:
                now = time.monotonic()
                readable, _, _ = select.select(
                    [stratum_sock, fpga_sock], [], [], 0.25
                )

                if stratum_sock in readable:
                    messages, closed = session.receive_available()
                    notify_changed = False
                    for message in messages:
                        message_id = message.get("id")
                        if (
                            pending_submit is not None
                            and isinstance(message_id, int)
                            and message_id == pending_submit["submit_id"]
                        ):
                            pending_submit["submit_response"] = message
                            latency = time.monotonic() - pending_since
                            pending_submit["response_latency_seconds"] = round(latency, 6)
                            if (
                                message.get("error") not in (None, False)
                                or message.get("result") is not True
                            ):
                                self.rejected += 1
                                pending_submit["accepted"] = False
                                self.shares.append(pending_submit)
                                self.checkpoint(force=True)
                                raise FatalMinerError(
                                    "DATUM rejected submit "
                                    f"id {message_id}: {message.get('error')}"
                                )
                            self.accepted += 1
                            self.accepted_diff_units += int(pending_submit["difficulty"])
                            if bool(pending_submit["network_target_meets"]):
                                self.network_target_shares += 1
                            self.last_accepted_monotonic = time.monotonic()
                            self.last_accepted_unix = time.time()
                            last_session_accept = self.last_accepted_monotonic
                            accepted_elapsed = (
                                self.last_accepted_monotonic - self.started_monotonic
                            )
                            if self.first_accepted_seconds is None:
                                self.first_accepted_seconds = accepted_elapsed
                            self.accepted_times.append(accepted_elapsed)
                            self.accepted_samples.append(
                                (
                                    self.last_accepted_monotonic,
                                    int(pending_submit["difficulty"]),
                                )
                            )
                            self.response_latencies.append(latency)
                            pending_submit["accepted"] = True
                            pending_submit["accepted_unix"] = self.last_accepted_unix
                            pending_submit["accepted_elapsed_seconds"] = round(
                                accepted_elapsed, 6
                            )
                            self.shares.append(pending_submit)
                            pending_submit = None
                            self.checkpoint(force=True)
                            continue
                        if process_control(message):
                            notify_changed = True
                    if notify_changed:
                        dispatch_work("notify")
                    if closed:
                        raise RecoverableMinerError("DATUM closed the Stratum session")

                if fpga_sock in readable:
                    frames, closed = hardware.receive_available(fpga_sock)
                    self.ignored_transport_bytes += hardware.ignored_bytes
                    hardware.ignored_bytes = 0
                    if closed:
                        raise RecoverableMinerError("FK33 transport closed")
                    for frame in frames:
                        try:
                            share = self.adapter.decode_share(frame)
                        except self.adapter.ProtocolError as exc:
                            self.invalid_frames += 1
                            raise FatalMinerError(
                                f"invalid hardware share frame: {exc}"
                            ) from exc
                        require(current_work is not None, "share arrived without active work")
                        if int(share["tag"]) != int(current_work["tag"]):
                            self.stale_returns += 1
                            continue
                        if pending_submit is not None:
                            self.shares_while_response_pending += 1
                            continue
                        if STOP_REQUESTED:
                            break

                        translation = dict(current_work["translation"])
                        asic80 = bytes.fromhex(translation["asic80"])
                        nonce = int(share["nonce"])
                        candidate = self.adapter.apply_returned_nonce(asic80, nonce)
                        raw_digest, final_digest = self.adapter.hash_candidate(
                            candidate, bytes(32)
                        )
                        target = int(translation["target_numeric"], 16)
                        require(
                            bytes(share["digest_wire"]) == bytes(32),
                            "lean share digest field is not zero",
                        )
                        require(
                            self.adapter.meets_target(final_digest, target),
                            "FPGA returned a host-invalid share",
                        )
                        network_target = compact_target(translation["nbits"])
                        request_id = self.next_submit_id
                        self.next_submit_id += 1
                        request = make_submit_request(
                            translation, self.args.username, candidate, request_id
                        )
                        session.send(request)
                        self.submissions += 1
                        pending_since = time.monotonic()
                        pending_submit = {
                            "schema": "fk33-blake2b-continuous-share-v3",
                            "session_number": session_number,
                            "submit_id": request_id,
                            "submitted_unix": time.time(),
                            "job_id": translation["job_id"],
                            "difficulty": translation["difficulty"],
                            "tag": translation["tag"],
                            "dispatch_index": current_work["dispatch_index"],
                            "dispatch_reason": current_work["reason"],
                            "extranonce2": translation["extranonce2"],
                            "returned_nonce": f"{nonce:08x}",
                            "nonce8_submit": candidate[32:40].hex(),
                            "raw_digest": raw_digest.hex(),
                            "final_digest": final_digest.hex(),
                            "share_target_numeric": translation["target_numeric"],
                            "network_target_numeric": f"{network_target:064x}",
                            "share_target_meets": True,
                            "network_target_meets": self.adapter.meets_target(
                                final_digest, network_target
                            ),
                            "job_frame_sha256": translation["job_frame_sha256"],
                            "share_frame_sha256": hashlib.sha256(frame).hexdigest(),
                            "submit_request": request,
                        }
                        if not STOP_REQUESTED:
                            dispatch_work("post-share")

                now = time.monotonic()
                if pending_submit is not None and now - pending_since >= self.args.response_timeout:
                    pending_submit["accepted"] = None
                    pending_submit["failure"] = "submit response timeout"
                    self.shares.append(pending_submit)
                    self.unknown_submissions += 1
                    pending_submit = None
                    raise RecoverableMinerError("submit response timed out")
                if now - last_session_accept >= self.args.share_watchdog_seconds:
                    raise RecoverableMinerError("accepted-share watchdog expired")
                if (
                    now - self.started_monotonic >= self.args.hashrate_watchdog_seconds
                    and not (
                        self.args.minimum_rolling_ghs
                        <= self.rolling_ghs()
                        <= self.args.maximum_rolling_ghs
                    )
                ):
                    raise RecoverableMinerError(
                        "30-minute rolling hashrate watchdog is outside "
                        f"{self.args.minimum_rolling_ghs:.2f}-"
                        f"{self.args.maximum_rolling_ghs:.2f} GH/s"
                    )
                if now >= next_roll_at:
                    dispatch_work("nonce-space-roll")
                self.checkpoint()
                self.progress()

            if pending_submit is not None:
                response_deadline = time.monotonic() + self.args.response_timeout
                while pending_submit is not None and time.monotonic() < response_deadline:
                    readable, _, _ = select.select([stratum_sock], [], [], 0.25)
                    if not readable:
                        continue
                    messages, closed = session.receive_available()
                    for message in messages:
                        if message.get("id") != pending_submit["submit_id"]:
                            continue
                        latency = time.monotonic() - pending_since
                        pending_submit["submit_response"] = message
                        pending_submit["response_latency_seconds"] = round(latency, 6)
                        if (
                            message.get("error") not in (None, False)
                            or message.get("result") is not True
                        ):
                            self.rejected += 1
                            pending_submit["accepted"] = False
                            self.shares.append(pending_submit)
                            raise FatalMinerError(
                                "DATUM rejected final submit: "
                                f"{message.get('error')}"
                            )
                        self.accepted += 1
                        self.accepted_diff_units += int(pending_submit["difficulty"])
                        if bool(pending_submit["network_target_meets"]):
                            self.network_target_shares += 1
                        self.last_accepted_monotonic = time.monotonic()
                        self.last_accepted_unix = time.time()
                        accepted_elapsed = (
                            self.last_accepted_monotonic - self.started_monotonic
                        )
                        if self.first_accepted_seconds is None:
                            self.first_accepted_seconds = accepted_elapsed
                        self.accepted_times.append(accepted_elapsed)
                        self.accepted_samples.append(
                            (
                                self.last_accepted_monotonic,
                                int(pending_submit["difficulty"]),
                            )
                        )
                        self.response_latencies.append(latency)
                        pending_submit["accepted"] = True
                        pending_submit["accepted_unix"] = self.last_accepted_unix
                        pending_submit["accepted_elapsed_seconds"] = round(
                            accepted_elapsed, 6
                        )
                        self.shares.append(pending_submit)
                        pending_submit = None
                        break
                    if pending_submit is None:
                        break
                    if closed:
                        break
                if pending_submit is not None:
                    pending_submit["accepted"] = None
                    pending_submit["failure"] = "stop before submit response"
                    self.shares.append(pending_submit)
                    self.unknown_submissions += 1
        finally:
            if (
                "pending_submit" in locals()
                and pending_submit is not None
                and "accepted" not in pending_submit
            ):
                pending_submit["accepted"] = None
                pending_submit["failure"] = "session ended before submit response"
                self.shares.append(pending_submit)
                self.unknown_submissions += 1
            session_duration = max(0.0, time.monotonic() - session_started)
            self.completed_active_seconds += session_duration
            self.current_session_started = None
            stratum_sock.close()
            fpga_sock.close()
            self.sessions_completed += 1
            self.current_job_id = None
            self.current_difficulty = None
            self.ignored_transport_bytes += hardware.ignored_bytes if "hardware" in locals() else 0
            self.record_session(
                "session-end",
                session_number=session_number,
                duration_seconds=round(session_duration, 6),
                accepted_shares=self.accepted - session_accepted_start,
                submissions=self.submissions - session_submissions_start,
                stop_requested=STOP_REQUESTED,
            )
            self.checkpoint(force=True)

    def run(self) -> int:
        self.status = "STARTING"
        self.checkpoint(force=True)
        recovery_started: float | None = None
        backoff = 1.0

        while not STOP_REQUESTED:
            try:
                accepted_before = self.accepted
                self.run_session()
                if STOP_REQUESTED:
                    break
                if self.accepted > accepted_before:
                    recovery_started = None
                    backoff = 1.0
            except RecoverableMinerError as exc:
                if STOP_REQUESTED:
                    break
                if self.accepted > accepted_before:
                    recovery_started = None
                    backoff = 1.0
                self.reconnects += 1
                self.recovery_failures += 1
                self.last_error = str(exc)
                self.status = "RECOVERING"
                now = time.monotonic()
                if recovery_started is None:
                    recovery_started = now
                self.record_session(
                    "recoverable-error",
                    reconnect_number=self.reconnects,
                    error=str(exc),
                    recovery_elapsed_seconds=round(now - recovery_started, 6),
                )
                self.checkpoint(force=True)
                print(
                    f"RECOVERY: error={exc} retry_in={backoff:.1f}s "
                    f"window={now - recovery_started:.1f}/{self.args.recovery_window_seconds:.0f}s",
                    flush=True,
                )
                if now - recovery_started >= self.args.recovery_window_seconds:
                    raise FatalMinerError(
                        "automatic recovery window expired after: " + str(exc)
                    ) from exc
                deadline = time.monotonic() + backoff
                while not STOP_REQUESTED and time.monotonic() < deadline:
                    time.sleep(0.1)
                backoff = min(self.args.maximum_reconnect_backoff, backoff * 2.0)

        self.status = "STOPPED"
        self.last_error = None
        self.checkpoint(force=True)
        self.progress(force=True)
        print("RESULT: FK33_BLAKE2B_CONTINUOUS_MINER_STOPPED_CLEANLY", flush=True)
        return 0


def run_selftest(adapter: ModuleType, translator: ModuleType, directory: Path) -> None:
    subscribe, _, notify = translator.fixture_messages()
    translation = translator.translate_job(
        adapter,
        subscribe,
        1,
        notify,
        "fk33-continuous-v3",
        extranonce2=(7).to_bytes(8, "little"),
    )
    validate_translation(translation)
    asic80 = bytes.fromhex(translation["asic80"])
    frame = adapter.encode_job(0x5A, asic80, DIFF1_TARGET)
    decoded = adapter.decode_job(frame)
    require(decoded["tag"] == 0x5A, "self-test tag changed")
    require(decoded["asic80"] == asic80, "self-test ASIC80 changed")
    candidate = adapter.apply_returned_nonce(asic80, 0x0BADF00D)
    request = make_submit_request(translation, "fk33-continuous-v3", candidate, 1000)
    require(request["params"][2] == "0700000000000000", "extranonce2 changed")
    require(request["params"][4] == candidate[32:40].hex(), "nonce8 mapping changed")

    directory.mkdir(parents=True, exist_ok=True)
    atomic_path = directory / "atomic-state-selftest.json"
    events_path = directory / "rotating-events-selftest.jsonl"
    write_json_atomic(atomic_path, {"result": "PASS"})
    require(json.loads(atomic_path.read_text())["result"] == "PASS", "atomic write failed")
    writer = RotatingJsonl(events_path, maximum_bytes=80, backups=2)
    for index in range(8):
        writer.append({"index": index, "payload": "x" * 20})
    require(events_path.is_file(), "rotating JSONL current file is missing")
    require(events_path.with_name(f"{events_path.name}.1").is_file(), "rotation failed")

    print("RESULT: FK33_BLAKE2B_CONTINUOUS_ENGINE_V3_SELFTEST_PASS")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--translator", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    selftest = subparsers.add_parser("selftest")
    selftest.add_argument("--directory", required=True, type=Path)

    live = subparsers.add_parser("run")
    live.add_argument("--stratum-host", default="127.0.0.1")
    live.add_argument("--stratum-port", type=int, default=23339)
    live.add_argument("--fpga-host", default="127.0.0.1")
    live.add_argument("--fpga-port", type=int, default=23000)
    live.add_argument("--username", default="fk33-continuous-v3")
    live.add_argument("--roll-seconds", type=float, default=5.0)
    live.add_argument("--handshake-timeout", type=float, default=45.0)
    live.add_argument("--response-timeout", type=float, default=30.0)
    live.add_argument("--share-watchdog-seconds", type=float, default=300.0)
    live.add_argument("--hashrate-watchdog-seconds", type=float, default=1800.0)
    live.add_argument("--minimum-rolling-ghs", type=float, default=0.50)
    live.add_argument("--maximum-rolling-ghs", type=float, default=1.50)
    live.add_argument("--recovery-window-seconds", type=float, default=300.0)
    live.add_argument("--maximum-reconnect-backoff", type=float, default=30.0)
    live.add_argument("--maximum-submits-per-session", type=int, default=1000000)
    live.add_argument("--progress-seconds", type=float, default=60.0)
    live.add_argument("--checkpoint-seconds", type=float, default=10.0)
    live.add_argument("--state-output", required=True, type=Path)
    live.add_argument("--shares-output", required=True, type=Path)
    live.add_argument("--stratum-events-output", required=True, type=Path)
    live.add_argument("--session-events-output", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        adapter = load_exact_module(args.adapter, ADAPTER_SHA256, "fk33_adapter_v3")
        translator = load_exact_module(
            args.translator, TRANSLATOR_SHA256, "fk33_translator_v3"
        )
        if args.command == "selftest":
            run_selftest(adapter, translator, args.directory)
            return 0

        require(args.stratum_host == "127.0.0.1", "Stratum host must be localhost")
        require(args.fpga_host == "127.0.0.1", "FPGA host must be localhost")
        require(args.roll_seconds == 5.0, "roll interval must be exactly five seconds")
        require(args.share_watchdog_seconds == 300.0, "share watchdog must be 300 seconds")
        require(
            args.hashrate_watchdog_seconds == 1800.0,
            "hashrate watchdog must use a 30-minute window",
        )
        require(args.minimum_rolling_ghs == 0.50, "minimum rolling hashrate changed")
        require(args.maximum_rolling_ghs == 1.50, "maximum rolling hashrate changed")
        require(args.recovery_window_seconds == 300.0, "recovery window must be 300 seconds")
        miner = ContinuousMiner(args, adapter, translator)
        return miner.run()
    except FatalMinerError as exc:
        print(f"FATAL: {exc}", file=sys.stderr, flush=True)
        if "miner" in locals():
            miner.status = "FATAL"
            miner.last_error = str(exc)
            miner.checkpoint(force=True)
        return 2
    except (ValueError, AssertionError, OSError, json.JSONDecodeError) as exc:
        print(f"FATAL: unexpected engine failure: {exc}", file=sys.stderr, flush=True)
        if "miner" in locals():
            miner.status = "FATAL"
            miner.last_error = str(exc)
            miner.checkpoint(force=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
