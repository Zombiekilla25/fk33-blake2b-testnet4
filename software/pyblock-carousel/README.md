# PyBLOCK Carousel runtime architecture

This directory publishes the sanitized software boundary from the 2026-08-31
deployment. Private LAN addresses, device serials, local paths, service names,
and site-specific launch wrappers are deliberately excluded.

## Components exercised

| Component | Role | SHA-256 |
|---|---|---|
| FK33 adapter | 121-byte job / 45-byte result transport | `c64d3d207be26ca268e9e12651dc2916331096d8c47359c9f6c58410bf231f1f` |
| PyBLOCK translator | reconstructs the BLAKE2b candidate from Stratum | `a33160ebd93a6eaa8549d32e730ac9485e1e89333bb6370c52fb2431b01992d1` |
| FK33 client v8 | rolling work, submit validation, disclosed 1% second session | `7c0b472dffc316788c478052398280328bdc6ffbaa2e45f7510785ff512c8151` |
| JCM33 dual client v2 | paired XVC work and disclosed 1% second session | `4b69047690e993420b7d725415f2cef302fd4637101fd662049fea594cf4c0fc` |

The FK33 client performs independent host-side candidate reconstruction before
submission. The JCM33 client reuses the same translation and submit rules while
tagging and validating each physical lane separately.

## Developer-fee contract

Both clients use a fixed 100-dispatch cycle. Slot zero uses a second Stratum
session authorized as
`bc1qe77h4ddu6cctl4zgxhy4wa6cf2z0gpsxw9dkvu.devfee`; the other 99 slots use
the configured user. Work records retain their role, and shares are submitted
only through the session that supplied the job. A developer-session outage
falls back to user work without accumulating debt or counting its failures as
user rejections.

The second socket uses subscribe ID `1` and authorize ID `2`, matching the
published translator's exact response validation. See
`tests/test_pyblock_devfee_runtime.py` for deterministic scheduling, real
translator handshake, worker authorization, and outage tests.

The dedicated supplier endpoint is `pool.pyblock.xyz:21020`. If that listener
is unavailable, `pool.pyblock.xyz:30110` is the documented manual fallback.
Configure both the user and developer Stratum sessions with the same selected
port; these clients do not change ports automatically.

The live verification boundary is two established port-21020 connections for
each of two FK33 v8 processes and one JCM33 v2 process. That proves separate
sessions were connected; it does not prove a developer share was accepted.

The original short startup gates that demanded an accepted Carousel share were
invalid because the pool assigned difficulty 16,384. Production health gates
must validate authenticated Stratum, connected hardware, rising work-dispatch
counters, and zero error counters. Accepted-share qualification requires an
actual accepted share and cannot be inferred from job traffic.
