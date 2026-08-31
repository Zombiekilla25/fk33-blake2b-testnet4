# PyBLOCK Carousel runtime architecture

This directory records the sanitized software boundary from the 2026-08-31
deployment. Private LAN addresses, device serials, local paths, service names,
and site-specific launch wrappers are deliberately not published.

## Components exercised

| Component | Role | SHA-256 |
|---|---|---|
| FK33 adapter | 121-byte job / 45-byte result transport | `c64d3d207be26ca268e9e12651dc2916331096d8c47359c9f6c58410bf231f1f` |
| PyBLOCK translator | reconstructs the BLAKE2b candidate from Stratum | `a33160ebd93a6eaa8549d32e730ac9485e1e89333bb6370c52fb2431b01992d1` |
| FK33 client v7 | rolling work, submit validation, rejection accounting | `30f3af43c75f58aca97baa7fd23b20467f17f336523678b23733d2b52ff3f9c7` |
| JCM33 dual client v1 | paired work through one serialized XVC session | `d39b2e7235c655adc7d9f24a517904edd08e1641371783a1952a0fe1d2a47aa8` |

The FK33 client performs independent host-side candidate reconstruction before
submission. The JCM33 client reuses the same translation and submit rules while
tagging and validating each physical lane separately.

The original short startup gates that demanded an accepted Carousel share were
invalid because the pool assigned difficulty 16,384. Production health gates
must validate authenticated Stratum, connected hardware, rising work-dispatch
counters, and zero error counters. Accepted-share qualification requires an
actual accepted share and cannot be inferred from job traffic.

