#!/usr/bin/env python3
"""FK33 BLAKE2b Profile-0 BSCAN framing and verification helper."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


MAGIC = b"FJ"
VERSION = 1
JOB_TYPE = 1
SHARE_TYPE = 2
JOB_PAYLOAD_BYTES = 113
SHARE_PAYLOAD_BYTES = 37
JOB_FRAME_BYTES = 121
SHARE_FRAME_BYTES = 45

QUALIFIED_BIT_SHA256 = (
    "33880a2339d8b03db044f74e8258353f9c8f1e9832e74b52efccd18e2328872c"
)
QUALIFIED_PROTOCOL_SHA256 = (
    "ab0db4db3e49db874e8ee187b6b955266a0f901125edb82b132e398b1505c22d"
)

VECTOR0_ASIC80 = (
    "000000000000943aff74219e1f45899abfdf536373c0f2fc92e6fe58335cd0ad"
    "0df0ad0b4433221158020000efcdab897e6326906eaa52fe59e03a14f1dfb8dd"
    "5d6e78497e56a8a6e4f4fb4d385e43db"
)
VECTOR0_RAW = "4b495dcf05d70a49785b799b22284fbcd9dd1209237c53c87e4674b15587d704"

VECTOR1_ASIC80 = (
    "000000000000943aff74219e1f45899abfdf536373c0f2fc92e6fe58335cd0ad"
    "ffffffff4433221188776655efcdab89544a71e01a4c041c727e86ec7cb2c68c6"
    "2d9dcab0ee9b07cdaf1a59bf2e5d40b"
)
VECTOR1_RAW = "c31b24420d67f86e524f980a24a18e88f36c821046d5288251b5d88998c69f87"
VECTOR1_MASK = "00" * 31 + "01"
VECTOR1_FINAL = "c31b24420d67f86e524f980a24a18e88f36c821046d5288251b5d88998c69f86"


class ProtocolError(ValueError):
    """Raised when an FJ frame is malformed."""


def exact_hex(value: str, byte_count: int, name: str) -> bytes:
    text = value.strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) != byte_count * 2:
        raise ProtocolError(
            f"{name} must contain exactly {byte_count} bytes "
            f"({byte_count * 2} hex characters)"
        )
    try:
        data = bytes.fromhex(text)
    except ValueError as exc:
        raise ProtocolError(f"{name} is not valid hexadecimal") from exc
    return data


def parse_u8(value: str) -> int:
    number = int(value, 0)
    if not 0 <= number <= 0xFF:
        raise argparse.ArgumentTypeError("value must fit in one byte")
    return number


def parse_u32(value: str) -> int:
    number = int(value, 0)
    if not 0 <= number <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("value must fit in 32 bits")
    return number


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_frame(frame_type: int, payload: bytes) -> bytes:
    header = MAGIC + bytes((VERSION, frame_type)) + struct.pack("<H", len(payload))
    return header + payload + struct.pack("<H", crc16_ccitt_false(payload))


def decode_frame(frame: bytes, expected_type: int, expected_payload: int) -> bytes:
    if len(frame) < 8:
        raise ProtocolError("frame is shorter than the fixed header and CRC")
    if frame[:2] != MAGIC:
        raise ProtocolError("frame magic is not FJ")
    if frame[2] != VERSION:
        raise ProtocolError(f"unsupported frame version {frame[2]}")
    if frame[3] != expected_type:
        raise ProtocolError(
            f"frame type {frame[3]} does not match expected type {expected_type}"
        )
    payload_length = struct.unpack_from("<H", frame, 4)[0]
    if payload_length != expected_payload:
        raise ProtocolError(
            f"payload length {payload_length} does not match {expected_payload}"
        )
    expected_total = 6 + payload_length + 2
    if len(frame) != expected_total:
        raise ProtocolError(
            f"frame length {len(frame)} does not match declared length {expected_total}"
        )
    payload = frame[6:-2]
    received_crc = struct.unpack_from("<H", frame, len(frame) - 2)[0]
    calculated_crc = crc16_ccitt_false(payload)
    if received_crc != calculated_crc:
        raise ProtocolError(
            f"CRC mismatch: received={received_crc:04x} calculated={calculated_crc:04x}"
        )
    return payload


def make_asic80(
    prev_asic: bytes,
    nonce8_le: bytes,
    ntime8_le: bytes,
    mid: bytes,
) -> bytes:
    if len(prev_asic) != 32:
        raise ProtocolError("prev_asic must be 32 bytes")
    if len(nonce8_le) != 8:
        raise ProtocolError("nonce8_le must be 8 bytes")
    if len(ntime8_le) != 8:
        raise ProtocolError("ntime8_le must be 8 bytes")
    if len(mid) != 32:
        raise ProtocolError("mid must be 32 bytes")
    return prev_asic + nonce8_le + ntime8_le + mid


def encode_job(tag: int, asic80: bytes, target_numeric: int) -> bytes:
    if not 0 <= tag <= 0xFF:
        raise ProtocolError("tag must fit in one byte")
    if len(asic80) != 80:
        raise ProtocolError("ASIC input must be exactly 80 bytes")
    if not 0 <= target_numeric < (1 << 256):
        raise ProtocolError("target must fit in 256 bits")
    payload = bytes((tag,)) + asic80 + target_numeric.to_bytes(32, "little")
    if len(payload) != JOB_PAYLOAD_BYTES:
        raise AssertionError("internal job payload size error")
    frame = encode_frame(JOB_TYPE, payload)
    if len(frame) != JOB_FRAME_BYTES:
        raise AssertionError("internal job frame size error")
    return frame


def decode_job(frame: bytes) -> dict[str, object]:
    payload = decode_frame(frame, JOB_TYPE, JOB_PAYLOAD_BYTES)
    return {
        "tag": payload[0],
        "asic80": payload[1:81],
        "target_numeric": int.from_bytes(payload[81:113], "little"),
    }


def encode_share(tag: int, nonce: int, digest_wire: bytes = bytes(32)) -> bytes:
    if not 0 <= tag <= 0xFF:
        raise ProtocolError("tag must fit in one byte")
    if not 0 <= nonce <= 0xFFFFFFFF:
        raise ProtocolError("nonce must fit in 32 bits")
    if len(digest_wire) != 32:
        raise ProtocolError("share digest must be 32 bytes")
    payload = bytes((tag,)) + nonce.to_bytes(4, "little") + digest_wire
    return encode_frame(SHARE_TYPE, payload)


def decode_share(frame: bytes) -> dict[str, object]:
    payload = decode_frame(frame, SHARE_TYPE, SHARE_PAYLOAD_BYTES)
    return {
        "tag": payload[0],
        "nonce": int.from_bytes(payload[1:5], "little"),
        "digest_wire": payload[5:37],
    }


def apply_returned_nonce(asic80: bytes, nonce: int) -> bytes:
    if len(asic80) != 80:
        raise ProtocolError("ASIC input must be exactly 80 bytes")
    if not 0 <= nonce <= 0xFFFFFFFF:
        raise ProtocolError("nonce must fit in 32 bits")
    candidate = bytearray(asic80)
    candidate[32:36] = nonce.to_bytes(4, "little")
    return bytes(candidate)


def hash_candidate(asic80: bytes, xor_mask: bytes = bytes(32)) -> tuple[bytes, bytes]:
    if len(asic80) != 80:
        raise ProtocolError("ASIC input must be exactly 80 bytes")
    if len(xor_mask) != 32:
        raise ProtocolError("XOR mask must be exactly 32 bytes")
    raw = hashlib.blake2b(asic80, digest_size=32).digest()
    final = bytes(left ^ right for left, right in zip(raw, xor_mask))
    return raw, final


def meets_target(final_digest: bytes, target_numeric: int) -> bool:
    """Compare raw post-XOR BLAKE2b bytes using the node's PoW ordering.

    The RC2 node reverses the 32 raw BLAKE2b bytes when constructing its
    internal little-endian ``uint256``.  Comparing that internal value to the
    target is therefore equivalent to interpreting the raw post-XOR digest as
    one big-endian integer here.
    """
    if len(final_digest) != 32:
        raise ProtocolError("final digest must be exactly 32 bytes")
    return int.from_bytes(final_digest, "big") <= target_numeric


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def authenticate_file(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ProtocolError(f"{label} is missing or empty: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise ProtocolError(
            f"{label} checksum mismatch: expected={expected} actual={actual}"
        )
    print(f"PASS: {label} authenticated: {actual}")


def run_selftest(bitstream: Path | None, protocol: Path | None) -> None:
    print("===== FK33 BLAKE2B PROFILE-0 HOST ADAPTER SELF-TEST =====")
    if bitstream is not None:
        authenticate_file(bitstream, QUALIFIED_BIT_SHA256, "qualified bitstream")
    if protocol is not None:
        authenticate_file(protocol, QUALIFIED_PROTOCOL_SHA256, "BSCAN protocol")

    vectors = (
        (
            0x10,
            VECTOR0_ASIC80,
            VECTOR0_RAW,
            "00" * 32,
            VECTOR0_RAW,
            0x0BADF00D,
        ),
        (
            0x90,
            VECTOR1_ASIC80,
            VECTOR1_RAW,
            VECTOR1_MASK,
            VECTOR1_FINAL,
            0xFFFFFFFF,
        ),
    )

    for index, (tag, asic_hex, raw_hex, mask_hex, final_hex, nonce) in enumerate(vectors):
        asic80 = exact_hex(asic_hex, 80, f"vector {index} ASIC input")
        target = (1 << 256) - 1
        job_frame = encode_job(tag, asic80, target)
        decoded_job = decode_job(job_frame)
        assert decoded_job["tag"] == tag
        assert decoded_job["asic80"] == asic80
        assert decoded_job["target_numeric"] == target

        share_frame = encode_share(tag, nonce)
        decoded_share = decode_share(share_frame)
        assert decoded_share["tag"] == tag
        assert decoded_share["nonce"] == nonce
        assert decoded_share["digest_wire"] == bytes(32)

        candidate = apply_returned_nonce(
            decoded_job["asic80"], decoded_share["nonce"]  # type: ignore[arg-type]
        )
        raw, final = hash_candidate(candidate, bytes.fromhex(mask_hex))
        assert raw.hex() == raw_hex
        assert final.hex() == final_hex
        assert meets_target(final, target)

        print(
            f"PASS: vector={index} tag={tag:02x} nonce={nonce:08x} "
            f"job_bytes={len(job_frame)} share_bytes={len(share_frame)}"
        )
        print(f"      raw={raw.hex()}")
        print(f"      final={final.hex()}")

    first = bytes.fromhex(VECTOR0_ASIC80)
    rebuilt = make_asic80(first[:32], first[32:40], first[40:48], first[48:80])
    assert rebuilt == first

    corrupted = bytearray(encode_job(0x10, first, (1 << 256) - 1))
    corrupted[20] ^= 1
    try:
        decode_job(bytes(corrupted))
    except ProtocolError:
        print("PASS: corrupted job CRC was rejected")
    else:
        raise AssertionError("corrupted job frame was accepted")

    # Physical-FPGA evidence from the two difficulty-1 canaries.  The node's
    # final_hash buffer is reverse(raw_post_xor), so the raw digest beginning
    # with zero bytes is valid and the digest ending with zero bytes is not.
    diff1_target = 0x00000000FFFF0000 << 192
    node_valid_raw = bytes.fromhex(
        "00000000cde3af9823674eaf9bed30c9"
        "e9c99756263df43a3077e93152ac29f8"
    )
    repaired_invalid_raw = bytes.fromhex(
        "df07b5900b56080e6c7b9128838726bd"
        "ec53b6b8eb395d7999f25c7600000000"
    )
    assert meets_target(node_valid_raw, diff1_target)
    assert not meets_target(repaired_invalid_raw, diff1_target)
    assert int.from_bytes(node_valid_raw[::-1], "little") == int.from_bytes(
        node_valid_raw, "big"
    )
    print("PASS: raw digest target orientation matches node uint256 reversal")

    print("RESULT: OFFLINE_121_BYTE_JOB_AND_45_BYTE_SHARE_ROUNDTRIP_PASS")


def command_encode_job(args: argparse.Namespace) -> None:
    asic80 = exact_hex(args.asic80, 80, "ASIC input")
    target = int(args.target, 16)
    print(encode_job(args.tag, asic80, target).hex())


def command_sia_job(args: argparse.Namespace) -> None:
    asic80 = make_asic80(
        exact_hex(args.prev_asic, 32, "prev_asic"),
        exact_hex(args.nonce8_le, 8, "nonce8_le"),
        exact_hex(args.ntime8_le, 8, "ntime8_le"),
        exact_hex(args.mid, 32, "mid"),
    )
    target = int(args.target, 16)
    frame = encode_job(args.tag, asic80, target)
    print(json.dumps({"asic80": asic80.hex(), "frame": frame.hex()}, indent=2))


def command_decode_job(args: argparse.Namespace) -> None:
    decoded = decode_job(bytes.fromhex(args.frame))
    print(
        json.dumps(
            {
                "tag": f"{decoded['tag']:02x}",
                "asic80": decoded["asic80"].hex(),  # type: ignore[union-attr]
                "target_numeric": f"{decoded['target_numeric']:064x}",
            },
            indent=2,
        )
    )


def command_decode_share(args: argparse.Namespace) -> None:
    decoded = decode_share(bytes.fromhex(args.frame))
    print(
        json.dumps(
            {
                "tag": f"{decoded['tag']:02x}",
                "nonce": f"{decoded['nonce']:08x}",
                "digest_wire": decoded["digest_wire"].hex(),  # type: ignore[union-attr]
            },
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    selftest = subparsers.add_parser("selftest", help="run the offline protocol tests")
    selftest.add_argument("--bitstream", type=Path)
    selftest.add_argument("--protocol", type=Path)
    selftest.set_defaults(handler=lambda args: run_selftest(args.bitstream, args.protocol))

    encode = subparsers.add_parser("encode-job", help="encode an exact ASIC80 job")
    encode.add_argument("--tag", required=True, type=parse_u8)
    encode.add_argument("--asic80", required=True)
    encode.add_argument("--target", required=True, help="256-bit numeric target in big-endian hex")
    encode.set_defaults(handler=command_encode_job)

    sia = subparsers.add_parser("sia-job", help="pack Sia-Sv1 fields into an FK33 job")
    sia.add_argument("--tag", required=True, type=parse_u8)
    sia.add_argument("--prev-asic", required=True)
    sia.add_argument("--nonce8-le", required=True)
    sia.add_argument("--ntime8-le", required=True)
    sia.add_argument("--mid", required=True)
    sia.add_argument("--target", required=True, help="256-bit numeric target in big-endian hex")
    sia.set_defaults(handler=command_sia_job)

    decode_job_parser = subparsers.add_parser("decode-job", help="decode and verify a job frame")
    decode_job_parser.add_argument("frame")
    decode_job_parser.set_defaults(handler=command_decode_job)

    decode_share_parser = subparsers.add_parser(
        "decode-share", help="decode and verify a share frame"
    )
    decode_share_parser.add_argument("frame")
    decode_share_parser.set_defaults(handler=command_decode_share)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except (ProtocolError, ValueError, AssertionError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
