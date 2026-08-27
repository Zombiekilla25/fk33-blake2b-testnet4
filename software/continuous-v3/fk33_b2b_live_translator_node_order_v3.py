#!/usr/bin/env python3
"""Translate DATUM BIP110 Sia-Sv1 jobs into FK33 Profile-0 frames."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import socket
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any


ADAPTER_SHA256 = (
    "5bc0982b893cea747514c9005805df9b0d4f380fb94ca733a3eada378e73a3f7"
)
DIFF1_TARGET = 0x00000000FFFF0000 << 192
DEFAULT_EXTRANONCE2 = bytes(8)
MOCK_NONCE = 0x0BADF00D
ALLOWED_OUTBOUND_METHODS = frozenset(("mining.subscribe", "mining.authorize"))


class TranslationError(ValueError):
    """Raised when a Stratum message cannot be translated safely."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_adapter(path: Path) -> ModuleType:
    if not path.is_file() or path.stat().st_size == 0:
        raise TranslationError(f"adapter is missing or empty: {path}")
    actual = sha256_file(path)
    if actual != ADAPTER_SHA256:
        raise TranslationError(
            f"adapter checksum mismatch: expected={ADAPTER_SHA256} actual={actual}"
        )
    spec = importlib.util.spec_from_file_location("fk33_profile0_adapter", path)
    if spec is None or spec.loader is None:
        raise TranslationError("could not create adapter import specification")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def exact_hex(value: Any, size: int, name: str) -> bytes:
    if not isinstance(value, str) or len(value) != size * 2:
        raise TranslationError(
            f"{name} must be exactly {size} bytes ({size * 2} hex characters)"
        )
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise TranslationError(f"{name} is not hexadecimal") from exc


def parse_difficulty(value: Any) -> int:
    if isinstance(value, bool):
        raise TranslationError("difficulty cannot be Boolean")
    if isinstance(value, int):
        difficulty = value
    elif isinstance(value, float) and value.is_integer():
        difficulty = int(value)
    else:
        raise TranslationError("difficulty must be a positive integer")
    if not 1 <= difficulty <= 0xFFFFFFFFFFFFFFFF:
        raise TranslationError("difficulty is outside DATUM's uint64 range")
    return difficulty


def target_from_difficulty(difficulty: int) -> int:
    target = DIFF1_TARGET // difficulty
    if not 0 < target < (1 << 256):
        raise TranslationError("computed target is outside 256-bit range")
    return target


def parse_subscribe(message: dict[str, Any]) -> tuple[bytes, int]:
    if message.get("id") != 1 or message.get("error") not in (None, False):
        raise TranslationError("invalid mining.subscribe response")
    result = message.get("result")
    if not isinstance(result, list) or len(result) != 3:
        raise TranslationError("mining.subscribe result is not a three-field array")
    extranonce1 = exact_hex(result[1], 4, "extranonce1")
    extranonce2_size = result[2]
    if extranonce2_size != 8:
        raise TranslationError(
            f"DATUM extranonce2 size is {extranonce2_size}, expected 8"
        )
    return extranonce1, extranonce2_size


def parse_notify(message: dict[str, Any]) -> dict[str, Any]:
    if message.get("method") != "mining.notify":
        raise TranslationError("message is not mining.notify")
    params = message.get("params")
    if not isinstance(params, list) or len(params) != 9:
        raise TranslationError("mining.notify is not the nine-field Sia-Sv1 dialect")

    job_id, prev_hex, coinb1_hex, coinb2, branches, version, nbits, ntime8_hex, clean = params
    if not isinstance(job_id, str) or not job_id:
        raise TranslationError("job_id is missing")
    prev_asic = exact_hex(prev_hex, 32, "prev_asic")
    coinb1 = exact_hex(coinb1_hex, 39, "coinb1")
    if coinb1[:3] != bytes(3) or coinb1[35:] != bytes(4):
        raise TranslationError("coinb1 does not match 3*00 || h2 || 4*00")
    if coinb2 != "" or branches != []:
        raise TranslationError("unexpected Sia-Sv1 coinb2 or merkle branches")
    exact_hex(version, 4, "version")
    exact_hex(nbits, 4, "nbits")
    ntime8 = exact_hex(ntime8_hex, 8, "ntime8")
    if not isinstance(clean, bool):
        raise TranslationError("clean_jobs is not Boolean")

    return {
        "job_id": job_id,
        "prev_asic": prev_asic,
        "coinb1": coinb1,
        "version": version,
        "nbits": nbits,
        "ntime8": ntime8,
        "clean": clean,
    }


def job_tag(job_id: str) -> int:
    return hashlib.blake2s(job_id.encode("ascii"), digest_size=1).digest()[0]


def translate_job(
    adapter: ModuleType,
    subscribe: dict[str, Any],
    difficulty_value: Any,
    notify_message: dict[str, Any],
    username: str,
    extranonce2: bytes = DEFAULT_EXTRANONCE2,
) -> dict[str, Any]:
    extranonce1, extranonce2_size = parse_subscribe(subscribe)
    if len(extranonce2) != extranonce2_size:
        raise TranslationError("selected extranonce2 length does not match DATUM")
    difficulty = parse_difficulty(difficulty_value)
    job = parse_notify(notify_message)

    en12 = extranonce1 + extranonce2
    leaf = b"\x00" + job["coinb1"] + en12
    if len(leaf) != 52:
        raise AssertionError("internal Sia leaf size error")
    mid = hashlib.blake2b(leaf, digest_size=32).digest()
    nonce8 = bytes(8)
    asic80 = adapter.make_asic80(
        job["prev_asic"], nonce8, job["ntime8"], mid
    )
    target = target_from_difficulty(difficulty)
    tag = job_tag(job["job_id"])
    job_frame = adapter.encode_job(tag, asic80, target)
    decoded_job = adapter.decode_job(job_frame)
    if decoded_job["tag"] != tag:
        raise AssertionError("job tag changed during frame round-trip")
    if decoded_job["asic80"] != asic80:
        raise AssertionError("ASIC80 changed during frame round-trip")
    if decoded_job["target_numeric"] != target:
        raise AssertionError("target changed during frame round-trip")

    mock_share = adapter.encode_share(tag, MOCK_NONCE)
    decoded_share = adapter.decode_share(mock_share)
    candidate = adapter.apply_returned_nonce(asic80, decoded_share["nonce"])
    raw_digest = hashlib.blake2b(candidate, digest_size=32).digest()
    nonce8_submit = candidate[32:40]

    submit_preview = {
        "id": 3,
        "method": "mining.submit",
        "params": [
            username,
            job["job_id"],
            extranonce2.hex(),
            job["ntime8"].hex(),
            nonce8_submit.hex(),
        ],
    }

    return {
        "schema": "fk33-blake2b-live-translation-v1",
        "dialect": "SIA_SV1",
        "job_id": job["job_id"],
        "clean_jobs": job["clean"],
        "version": job["version"],
        "nbits": job["nbits"],
        "difficulty": difficulty,
        "extranonce1": extranonce1.hex(),
        "extranonce2": extranonce2.hex(),
        "en12": en12.hex(),
        "prev_asic": job["prev_asic"].hex(),
        "coinb1": job["coinb1"].hex(),
        "ntime8": job["ntime8"].hex(),
        "mid": mid.hex(),
        "asic80": asic80.hex(),
        "asic80_sha256": sha256_bytes(asic80),
        "target_numeric": f"{target:064x}",
        "tag": f"{tag:02x}",
        "job_frame": job_frame.hex(),
        "job_frame_bytes": len(job_frame),
        "job_frame_sha256": sha256_bytes(job_frame),
        "mock_share_frame": mock_share.hex(),
        "mock_share_frame_bytes": len(mock_share),
        "mock_nonce": f"{MOCK_NONCE:08x}",
        "mock_raw_digest": raw_digest.hex(),
        "mock_meets_target": adapter.meets_target(raw_digest, target),
        "submit_preview": submit_preview,
        "share_submitted": False,
    }


def fixture_messages() -> tuple[dict[str, Any], int, dict[str, Any]]:
    subscribe = {
        "error": None,
        "id": 1,
        "result": [
            [
                ["mining.notify", "b10cf00d1"],
                ["mining.set_difficulty", "b10cf00d2"],
            ],
            "b10cf00d",
            8,
        ],
    }
    notify = {
        "id": None,
        "method": "mining.notify",
        "params": [
            "6a8f884d02c0df00",
            "0000000000007df64ddb042364c3be94bc3839490434083a7173e002dac08229",
            "000000f1fe8bca84b248ffa7bf8bb3cd4143fc79e569d145f6e40f8e712324fd14dc3a00000000",
            "",
            [],
            "20000000",
            "1a03fffc",
            "0000000000000000",
            True,
        ],
    }
    return subscribe, 16384, notify


def run_selftest(adapter: ModuleType) -> dict[str, Any]:
    subscribe, difficulty, notify = fixture_messages()
    record = translate_job(
        adapter, subscribe, difficulty, notify, "fk33-software-translation"
    )
    expected = {
        "mid": "52f73beef184df56e591af2ceb33330d3b7b5c8359fc485eec7013690a9c788a",
        "asic80_sha256": "83d6e17628ebed2f3686e1defe53523bac9d6142326ff491a6bece0a272f2a6b",
        "target_numeric": "000000000003fffc000000000000000000000000000000000000000000000000",
        "tag": "e1",
        "job_frame_sha256": "a7c43be72e87cbe607c1a04c5a9f06d071d6abc6e46a12a747f31d09d41278a8",
        "mock_raw_digest": "8bbbe3e82cbac0380bf62d4b63d3acd09caac04dff062d7abc34c74a8228c39e",
    }
    for key, value in expected.items():
        if record[key] != value:
            raise AssertionError(
                f"fixture {key} mismatch: expected={value} actual={record[key]}"
            )
    if record["job_frame_bytes"] != 121:
        raise AssertionError("fixture job frame is not 121 bytes")
    if record["mock_share_frame_bytes"] != 45:
        raise AssertionError("fixture mock share frame is not 45 bytes")
    if record["share_submitted"] is not False:
        raise AssertionError("fixture unexpectedly marked a share submitted")
    return record


def send_json(sock: socket.socket, message: dict[str, Any]) -> None:
    method = message.get("method")
    if method not in ALLOWED_OUTBOUND_METHODS:
        raise TranslationError(f"outbound method is forbidden in capture mode: {method}")
    payload = json.dumps(message, separators=(",", ":")).encode() + b"\n"
    sock.sendall(payload)


def capture_live(
    adapter: ModuleType,
    host: str,
    port: int,
    username: str,
    timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    subscribe_message = {
        "id": 1,
        "method": "mining.subscribe",
        "params": ["fk33-live-translator/1.0"],
    }
    authorize_message = {
        "id": 2,
        "method": "mining.authorize",
        "params": [username, "x"],
    }

    received: list[dict[str, Any]] = []
    subscribe: dict[str, Any] | None = None
    notify: dict[str, Any] | None = None
    difficulty: Any = None
    authorized = False
    deadline = time.monotonic() + timeout

    with socket.create_connection((host, port), timeout=min(timeout, 10)) as sock:
        sock.settimeout(1)
        send_json(sock, subscribe_message)
        send_json(sock, authorize_message)
        buffer = b""
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                continue
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                if not raw.strip():
                    continue
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise TranslationError("Stratum response is not a JSON object")
                received.append(message)
                if message.get("id") == 1:
                    subscribe = message
                elif message.get("id") == 2:
                    authorized = message.get("result") is True
                elif message.get("method") == "mining.set_difficulty":
                    params = message.get("params")
                    if not isinstance(params, list) or len(params) != 1:
                        raise TranslationError("invalid mining.set_difficulty message")
                    difficulty = params[0]
                elif message.get("method") == "mining.notify":
                    notify = message

                if (
                    subscribe is not None
                    and notify is not None
                    and difficulty is not None
                    and authorized
                ):
                    record = translate_job(
                        adapter,
                        subscribe,
                        difficulty,
                        notify,
                        username,
                    )
                    return record, received

    missing = []
    if subscribe is None:
        missing.append("subscribe")
    if difficulty is None:
        missing.append("difficulty")
    if notify is None:
        missing.append("notify")
    if not authorized:
        missing.append("authorize")
    raise TranslationError("live capture timed out; missing: " + ", ".join(missing))


def write_json(path: Path | None, value: dict[str, Any]) -> None:
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(text, end="")
        return
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    selftest = subparsers.add_parser("selftest")
    selftest.add_argument("--output", type=Path)

    capture = subparsers.add_parser("capture")
    capture.add_argument("--host", default="127.0.0.1")
    capture.add_argument("--port", type=int, default=23339)
    capture.add_argument("--user", default="fk33-software-translation")
    capture.add_argument("--timeout", type=float, default=45.0)
    capture.add_argument("--output", required=True, type=Path)
    capture.add_argument("--messages-output", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        adapter = load_adapter(args.adapter)
        if args.command == "selftest":
            record = run_selftest(adapter)
            write_json(args.output, record)
            print("RESULT: LIVE_TRANSLATOR_FIXTURE_PASS", file=sys.stderr)
        else:
            record, messages = capture_live(
                adapter, args.host, args.port, args.user, args.timeout
            )
            write_json(args.output, record)
            write_json(args.messages_output, {"messages": messages})
            print("RESULT: LIVE_DATUM_TO_FK33_JOB_TRANSLATION_PASS")
            print("PASS: mining.submit was previewed but not transmitted")
        return 0
    except (TranslationError, ValueError, AssertionError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
