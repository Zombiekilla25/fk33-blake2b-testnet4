#!/usr/bin/env python3
"""Mine PyBLOCK Carousel with both JCM33 BLAKE2b lanes through one XVC session."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import select
import socket
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any


ADAPTER_SHA256 = "c64d3d207be26ca268e9e12651dc2916331096d8c47359c9f6c58410bf231f1f"
TRANSLATOR_SHA256 = "a33160ebd93a6eaa8549d32e730ac9485e1e89333bb6370c52fb2431b01992d1"
FK_CLIENT_SHA256 = "7c0b472dffc316788c478052398280328bdc6ffbaa2e45f7510785ff512c8151"
SUBSCRIBE_ID = 1
AUTHORIZE_ID = 2
FIRST_SUBMIT_ID = 1000


class LiveError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LiveError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_module(path: Path, name: str, expected_sha256: str | None = None) -> ModuleType:
    require(path.is_file() and path.stat().st_size > 0, f"missing module: {path}")
    actual = sha256_file(path)
    if expected_sha256 is not None:
        require(
            actual == expected_sha256,
            f"{name} checksum mismatch expected={expected_sha256} actual={actual}",
        )
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
    path.chmod(0o600)


def load_jcm_module(path: Path) -> ModuleType:
    module_dir = str(path.parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    return load_module(path, "jcm33_transport")


class Blake2bDualTransport:
    """Use the qualified JCM33 lane alignment with BLAKE2b frames."""

    def __init__(self, jcm: ModuleType, host: str, port: int) -> None:
        self.jcm = jcm
        self.transport = jcm.HardwareTransport(host, port)

    def connect(self) -> None:
        self.transport.connect()

    def close(self) -> None:
        self.transport.close()

    def send_frames(self, frames: dict[str, bytes]) -> None:
        programs = []
        for lane_name, lane_index in (("A", 0), ("B", 1)):
            frame = frames.get(lane_name)
            if frame is not None:
                require(len(frame) == 121, f"lane {lane_name} job frame is not 121 bytes")
                programs.append(self.jcm.build_per_byte_write(lane_index, frame))
        require(bool(programs), "no JCM33 job frame selected")
        combined = self.jcm.concatenate_programs(*programs)
        self.transport.ensure_connected()
        require(
            len(combined.tms) <= self.transport.max_bits,
            f"JCM33 job transaction exceeds XVC limit: {len(combined.tms)}",
        )
        try:
            self.transport.xvc.shift(combined.tms, combined.tdi)
        except (OSError, RuntimeError, ValueError) as exc:
            self.transport.close()
            raise LiveError(f"JCM33 job write failed: {exc}") from exc

    def pop_share_frames(self) -> list[tuple[str, bytes]]:
        frames: list[tuple[str, bytes]] = []
        for lane_name, lane_index in (("A", 0), ("B", 1)):
            self.transport._read_lane(lane_name, lane_index)
            while True:
                parsed = self.transport._pop_frame(lane_name)
                if parsed is None:
                    break
                frame_type, payload = parsed
                if frame_type != self.jcm.FRAME_SHARE or len(payload) != 37:
                    print(
                        f"IGNORED lane={lane_name} frame_type={frame_type} "
                        f"payload_bytes={len(payload)}",
                        flush=True,
                    )
                    continue
                frames.append((lane_name, self.jcm.encode_frame(frame_type, payload)))
        return frames


def handle_control_message(
    message: dict[str, Any],
    translator: ModuleType,
    state: dict[str, Any],
) -> tuple[bool, bool]:
    """Return (work_changed, response_consumed)."""
    message_id = message.get("id")
    if message_id == SUBSCRIBE_ID:
        translator.parse_subscribe(message)
        state["subscribe"] = message
        return True, True
    if message_id == AUTHORIZE_ID:
        require(message.get("error") in (None, False), "Carousel authorization error")
        require(message.get("result") is True, "Carousel rejected authorization")
        state["authorized"] = True
        return False, True
    if message.get("method") == "mining.set_difficulty":
        params = message.get("params")
        require(isinstance(params, list) and len(params) == 1, "invalid difficulty message")
        state["difficulty"] = translator.parse_difficulty(params[0])
        return True, True
    if message.get("method") == "mining.notify":
        translator.parse_notify(message)
        state["notify"] = message
        return True, True
    return False, False


def run(args: argparse.Namespace) -> None:
    adapter = load_module(args.adapter, "fk33_adapter", ADAPTER_SHA256)
    translator = load_module(args.translator, "fk33_translator", TRANSLATOR_SHA256)
    fk_client = load_module(args.fk_client, "fk33_client", FK_CLIENT_SHA256)
    jcm = load_jcm_module(args.jcm_miner)

    transport = Blake2bDualTransport(jcm, args.xvc_host, args.xvc_port)
    transport.connect()

    accepted = {"A": 0, "B": 0}
    rejected = {"A": 0, "B": 0}
    devfee_accepted = {"A": 0, "B": 0}
    devfee_rejected = {"A": 0, "B": 0}
    invalid = {"A": 0, "B": 0}
    stale = {"A": 0, "B": 0}
    state: dict[str, Any] = {
        "subscribe": None,
        "difficulty": None,
        "notify": None,
        "authorized": False,
    }
    pending: dict[int, dict[str, Any]] = {}
    jobs: dict[tuple[str, int], dict[str, Any]] = {}
    seen_shares: set[tuple[str, str, str, int]] = set()
    next_submit_id = FIRST_SUBMIT_ID
    extranonce2_counter = 0
    tag_counter = 1
    started = time.monotonic()
    next_progress = started + args.progress_seconds
    next_roll = started
    scheduler = fk_client.DevFeeScheduler(args.devfee_cycle_sweeps, 1)
    devfee = fk_client.AuxiliaryStratum(
        args.devfee_host,
        args.devfee_port,
        args.devfee_username,
        translator,
        args.maximum_submits,
        args.devfee_handshake_timeout,
        args.devfee_reconnect_seconds,
    )

    def dispatch_pair(reason: str) -> None:
        nonlocal extranonce2_counter, tag_counter, next_roll
        role = scheduler.choose(devfee.ready)
        source_state = devfee.state if role == "developer" else state
        subscribe = source_state["subscribe"]
        difficulty = source_state["difficulty"]
        notify = source_state["notify"]
        work_username = devfee.username if role == "developer" else args.username
        require(subscribe is not None, "work dispatch lacks subscription")
        require(difficulty is not None, "work dispatch lacks difficulty")
        require(notify is not None, "work dispatch lacks notify")
        _, extranonce2_size = translator.parse_subscribe(subscribe)
        modulus = 1 << (8 * extranonce2_size)
        frames: dict[str, bytes] = {}

        for lane_name in ("A", "B"):
            extranonce2 = (extranonce2_counter % modulus).to_bytes(
                extranonce2_size, "little"
            )
            extranonce2_counter += 1
            translation = translator.translate_job(
                adapter,
                subscribe,
                difficulty,
                notify,
                work_username,
                extranonce2=extranonce2,
            )
            tag = tag_counter & 0xFF
            tag_counter = (tag_counter + 1) & 0xFF
            asic80 = bytes.fromhex(translation["asic80"])
            target = int(translation["target_numeric"], 16)
            frame = adapter.encode_job(tag, asic80, target)
            decoded = adapter.decode_job(frame)
            require(decoded["tag"] == tag, "JCM33 job tag round-trip failed")
            require(decoded["asic80"] == asic80, "JCM33 ASIC80 round-trip failed")
            require(decoded["target_numeric"] == target, "JCM33 target round-trip failed")
            translation["tag"] = f"{tag:02x}"
            translation["job_frame"] = frame.hex()
            translation["job_frame_bytes"] = len(frame)
            translation["job_frame_sha256"] = hashlib.sha256(frame).hexdigest()
            jobs[(lane_name, tag)] = {
                "translation": translation,
                "dispatched_at": time.monotonic(),
                "reason": reason,
                "mining_role": role,
                "submit_username": work_username,
            }
            frames[lane_name] = frame

        transport.send_frames(frames)
        next_roll = time.monotonic() + args.roll_seconds
        print(
            f"DISPATCH reason={reason} difficulty={difficulty} "
            f"role={role} "
            f"tags=A:{adapter.decode_job(frames['A'])['tag']:02x},"
            f"B:{adapter.decode_job(frames['B'])['tag']:02x}",
            flush=True,
        )

    try:
        with socket.create_connection(
            (args.pool_host, args.pool_port), timeout=args.handshake_timeout
        ) as stratum_sock:
            stratum_sock.setblocking(False)
            session = fk_client.StratumSession(stratum_sock, args.maximum_submits)
            session.send(
                {
                    "id": SUBSCRIBE_ID,
                    "method": "mining.subscribe",
                    "params": ["jcm33-duallane-pyblock-carousel/1.0"],
                }
            )
            session.send(
                {
                    "id": AUTHORIZE_ID,
                    "method": "mining.authorize",
                    "params": [args.username, "x"],
                }
            )

            handshake_deadline = time.monotonic() + args.handshake_timeout
            while time.monotonic() < handshake_deadline:
                readable, _, _ = select.select([stratum_sock], [], [], 1.0)
                if not readable:
                    continue
                messages, closed = session.receive_available()
                require(not closed, "Carousel closed during handshake")
                for message in messages:
                    handle_control_message(message, translator, state)
                if (
                    state["subscribe"] is not None
                    and state["difficulty"] is not None
                    and state["notify"] is not None
                    and state["authorized"]
                ):
                    break

            require(state["subscribe"] is not None, "subscribe response timed out")
            require(state["difficulty"] is not None, "difficulty announcement timed out")
            require(state["notify"] is not None, "mining job timed out")
            require(state["authorized"], "authorization timed out")
            print(
                f"CAROUSEL_READY pool={args.pool_host}:{args.pool_port} "
                f"username={args.username} difficulty={state['difficulty']}",
                flush=True,
            )
            devfee.maybe_connect()
            dispatch_pair("initial")

            while True:
                if not devfee.ready:
                    devfee.maybe_connect()
                now = time.monotonic()
                timeout = min(0.10, max(0.0, next_roll - now))
                read_sockets = [stratum_sock]
                if devfee.sock is not None:
                    read_sockets.append(devfee.sock)
                readable, _, _ = select.select(read_sockets, [], [], timeout)
                work_changed = False

                if stratum_sock in readable:
                    messages, closed = session.receive_available()
                    require(not closed, "Carousel closed the Stratum connection")
                    for message in messages:
                        message_id = message.get("id")
                        if isinstance(message_id, int) and message_id in pending:
                            record = pending.pop(message_id)
                            lane_name = record["lane"]
                            record["response"] = message
                            record["response_latency_seconds"] = round(
                                time.monotonic() - record["submitted_at"], 6
                            )
                            if (
                                message.get("error") in (None, False)
                                and message.get("result") is True
                            ):
                                accepted[lane_name] += 1
                                record["accepted"] = True
                                print(
                                    f"ACCEPTED lane={lane_name} id={message_id} "
                                    f"nonce={record['nonce']}",
                                    flush=True,
                                )
                            else:
                                rejected[lane_name] += 1
                                record["accepted"] = False
                                print(
                                    f"REJECTED lane={lane_name} id={message_id} "
                                    f"error={message.get('error')} (continuing)",
                                    flush=True,
                                )
                            append_jsonl(args.shares_output, record)
                            continue
                        changed, _ = handle_control_message(message, translator, state)
                        work_changed = work_changed or changed

                if work_changed and (
                    state["subscribe"] is not None
                    and state["difficulty"] is not None
                    and state["notify"] is not None
                ):
                    dispatch_pair("stratum-update")

                if devfee.sock is not None and devfee.sock in readable:
                    for message in devfee.receive_available():
                        message_id = message.get("id")
                        if not isinstance(message_id, int) or message_id not in pending:
                            continue
                        record = pending.pop(message_id)
                        lane_name = record["lane"]
                        record["response"] = message
                        record["response_latency_seconds"] = round(
                            time.monotonic() - record["submitted_at"], 6
                        )
                        if (
                            message.get("error") in (None, False)
                            and message.get("result") is True
                        ):
                            devfee_accepted[lane_name] += 1
                            record["accepted"] = True
                            print(
                                f"ACCEPTED role=developer lane={lane_name} "
                                f"id={message_id} nonce={record['nonce']}",
                                flush=True,
                            )
                        else:
                            devfee_rejected[lane_name] += 1
                            record["accepted"] = False
                            print(
                                f"REJECTED role=developer lane={lane_name} "
                                f"id={message_id} error={message.get('error')} (continuing)",
                                flush=True,
                            )
                        append_jsonl(args.shares_output, record)

                for lane_name, frame in transport.pop_share_frames():
                    share = adapter.decode_share(frame)
                    tag = int(share["tag"])
                    job = jobs.get((lane_name, tag))
                    if job is None:
                        stale[lane_name] += 1
                        print(
                            f"STALE_FRAME lane={lane_name} tag={tag:02x} no_job=1",
                            flush=True,
                        )
                        continue
                    translation = job["translation"]
                    role = job["mining_role"]
                    nonce = int(share["nonce"])
                    duplicate_key = (
                        lane_name,
                        translation["job_id"],
                        translation["extranonce2"],
                        nonce,
                    )
                    if duplicate_key in seen_shares:
                        continue
                    seen_shares.add(duplicate_key)
                    asic80 = bytes.fromhex(translation["asic80"])
                    candidate = adapter.apply_returned_nonce(asic80, nonce)
                    digest = hashlib.blake2b(candidate, digest_size=32).digest()
                    target = int(translation["target_numeric"], 16)
                    if not adapter.meets_target(digest, target):
                        invalid[lane_name] += 1
                        print(
                            f"HOST_INVALID lane={lane_name} tag={tag:02x} "
                            f"nonce={nonce:08x} digest={digest.hex()}",
                            flush=True,
                        )
                        continue
                    request_id = (
                        fk_client.DEVFEE_FIRST_SUBMIT_ID + next_submit_id
                        if role == "developer" else next_submit_id
                    )
                    next_submit_id += 1
                    request = fk_client.make_submit_request(
                        translation, job["submit_username"], candidate, request_id
                    )
                    if role == "developer":
                        if not devfee.ready or devfee.session is None:
                            stale[lane_name] += 1
                            continue
                        devfee.session.send(request)
                    else:
                        session.send(request)
                    pending[request_id] = {
                        "schema": "jcm33-pyblock-carousel-duallane-share-v1",
                        "lane": lane_name,
                        "submit_id": request_id,
                        "job_id": translation["job_id"],
                        "difficulty": translation["difficulty"],
                        "tag": f"{tag:02x}",
                        "extranonce2": translation["extranonce2"],
                        "nonce": f"{nonce:08x}",
                        "digest": digest.hex(),
                        "target": translation["target_numeric"],
                        "job_frame_sha256": translation["job_frame_sha256"],
                        "share_frame_sha256": hashlib.sha256(frame).hexdigest(),
                        "submit_request": request,
                        "submitted_at": time.monotonic(),
                        "mining_role": role,
                    }

                now = time.monotonic()
                expired = [
                    request_id
                    for request_id, record in pending.items()
                    if now - record["submitted_at"] > args.response_timeout
                ]
                for request_id in expired:
                    record = pending.pop(request_id)
                    lane_name = record["lane"]
                    if record["mining_role"] == "developer":
                        devfee_rejected[lane_name] += 1
                    else:
                        rejected[lane_name] += 1
                    record["accepted"] = False
                    record["response"] = {"error": "response-timeout"}
                    append_jsonl(args.shares_output, record)
                    print(
                        f"REJECTED lane={lane_name} id={request_id} "
                        "error=response-timeout (continuing)",
                        flush=True,
                    )

                if now >= next_roll:
                    dispatch_pair("nonce-space-roll")

                if now >= next_progress:
                    elapsed = now - started
                    total_accepted = accepted["A"] + accepted["B"]
                    total_rejected = rejected["A"] + rejected["B"]
                    estimated_ghs = (
                        total_accepted
                        * float(fk_client.EXPECTED_HASHES_PER_DIFF1)
                        / elapsed
                        / 1e9
                    )
                    print(
                        "PROGRESS: "
                        f"elapsed={elapsed:.1f}s accepted={total_accepted} "
                        f"rejected={total_rejected} estimated={estimated_ghs:.3f}GH/s "
                        f"devfee_accepted=A:{devfee_accepted['A']},B:{devfee_accepted['B']} "
                        f"devfee_rejected=A:{devfee_rejected['A']},B:{devfee_rejected['B']} "
                        f"laneA_accepted={accepted['A']} laneB_accepted={accepted['B']} "
                        f"invalid=A:{invalid['A']},B:{invalid['B']} "
                        f"stale=A:{stale['A']},B:{stale['B']} "
                        f"difficulty={state['difficulty']} "
                        f"devfee={scheduler.snapshot()}",
                        flush=True,
                    )
                    next_progress = now + args.progress_seconds
                    if len(session.events) > 10000:
                        del session.events[:-1000]
                    if len(seen_shares) > 100000:
                        seen_shares.clear()
    finally:
        devfee.close()
        transport.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--adapter", required=True, type=Path)
    result.add_argument("--translator", required=True, type=Path)
    result.add_argument("--fk-client", required=True, type=Path)
    result.add_argument("--jcm-miner", required=True, type=Path)
    result.add_argument("--pool-host", default="pool.pyblock.xyz")
    result.add_argument("--pool-port", type=int, default=21020)
    result.add_argument("--username", required=True)
    result.add_argument("--xvc-host", default="127.0.0.1")
    result.add_argument("--xvc-port", type=int, default=2542)
    result.add_argument("--roll-seconds", type=float, default=2.5)
    result.add_argument("--handshake-timeout", type=float, default=60.0)
    result.add_argument("--response-timeout", type=float, default=30.0)
    result.add_argument("--progress-seconds", type=float, default=60.0)
    result.add_argument("--maximum-submits", type=int, default=1000000)
    result.add_argument("--devfee-host", default="pool.pyblock.xyz")
    result.add_argument("--devfee-port", type=int, default=21020)
    result.add_argument("--devfee-username", default=(
        "bc1qe77h4ddu6cctl4zgxhy4wa6cf2z0gpsxw9dkvu.devfee"
    ))
    result.add_argument("--devfee-cycle-sweeps", type=int, choices=(100,), default=100)
    result.add_argument("--devfee-handshake-timeout", type=float, default=3.0)
    result.add_argument("--devfee-reconnect-seconds", type=float, default=60.0)
    result.add_argument("--shares-output", required=True, type=Path)
    return result


if __name__ == "__main__":
    run(parser().parse_args())
