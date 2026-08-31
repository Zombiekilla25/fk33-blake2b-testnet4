# PyBLOCK Carousel mainnet deployment

This document records the public, sanitized BLAKE2b mainnet deployment boundary
observed on 2026-08-31. It is a reproducibility record, not a profitability
promise. Private LAN addresses, device serials, control ports, local paths, and
host-specific launch commands are deliberately excluded.

## Pool interface

```text
Pool:      stratum+tcp://pool.pyblock.xyz:30110
Username:  YOUR_BTC_ADDRESS.worker
Password:  x
```

Only a public payout address is required. Never provide a private key, seed
phrase, wallet file, RPC cookie, or RPC password to a miner or pool.

## Active hardware topology

| Group | Physical devices | Public worker model |
|---|---:|---|
| Primary FK33 fleet | 6 FK33s | one worker suffix per FPGA |
| Secondary FK33 fleet | 2 FK33s | one worker suffix per FPGA |
| JCM33 carrier | FPGA A and FPGA B | one paired JCM33 worker |

Each image contains six BLAKE2b lanes at 195 MHz, a nominal 1.170 GH/s per
FPGA. Ten FPGA devices therefore represent 11.7 GH/s of nominal datapath
capacity. That clock-derived number is not a pool measurement.

## Authenticated artifacts

| Artifact | SHA-256 |
|---|---|
| FK33 six-lane timing-margin bitstream | `8af69b85767fd6edfde274194813e204129ea125321ba147561f400385e81e86` |
| JCM33 dual-FPGA six-lane bitstream | `942ebd3f3fce3a6ac192c12c895d0702b160ad35d8a0e6966b1239a4466d4169` |
| FK33 adapter | `c64d3d207be26ca268e9e12651dc2916331096d8c47359c9f6c58410bf231f1f` |
| Sia-Sv1/PyBLOCK translator | `a33160ebd93a6eaa8549d32e730ac9485e1e89333bb6370c52fb2431b01992d1` |
| PyBLOCK FK33 client v7 | `30f3af43c75f58aca97baa7fd23b20467f17f336523678b23733d2b52ff3f9c7` |
| JCM33 dual client v1 | `d39b2e7235c655adc7d9f24a517904edd08e1641371783a1952a0fe1d2a47aa8` |

## Validation completed before Carousel

- One FK33 returned a host-verified physical BLAKE2b share at local difficulty
  1 without a pool connection or `mining.submit`.
- The six-FK path completed a low-difficulty PyBLOCK interoperability run with
  accepted shares on every worker. That short run recorded 178 accepted and 18
  pool-side `high-hash` rejections while the rejection-tolerant v7 client
  continued operating.
- JCM33 FPGA A and B passed the paced five-minute transport soak: 120 verified
  rounds per lane, with no invalid or stale lane frames.
- The JCM33 XVC chain authenticated two devices and a 12-bit IR chain before
  mainnet work was dispatched.

## Carousel observation

Carousel negotiated difficulty 16,384. At the recorded snapshot:

- all eight FK33 clients were active and continuously rolling jobs;
- JCM33 FPGA A and B received paired work through one serialized XVC session;
- rejected, invalid, and stale counters were zero;
- accepted counters remained zero.

At 1.170 GH/s, the mean time to one difficulty-16,384 share is about 16.7 hours
per FPGA. At 11.7 GH/s aggregate nominal capacity, the fleet-wide mean is about
1.67 hours. Share arrival is Poisson-distributed, so a substantially shorter or
longer wait is normal. The client reports `estimated=0.000GH/s` until its first
accepted share because the estimate is share-derived.

## Healthy pre-acceptance state

- every miner process remains active;
- `current_diff=16384`;
- `jobs` and `timed_rolls` increase;
- `rejected=0`;
- each FPGA transport remains connected.

Stop and investigate persistent rejection, invalid-frame, stale-frame,
checksum, identity, or control-path errors. Pool authorization and job counters
prove work dispatch, not a pool-accepted share.

