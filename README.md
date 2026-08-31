# FK33 and JCM33 BLAKE2b FPGA reference

Experimental BLAKE2b FPGA mining research for SQRL FK33 and JCM33 hardware.
The repository now preserves both the original qualified Testnet4 lineage and
the six-lane 195 MHz mainnet/PyBLOCK deployment exercised on 2026-08-31.

## Current status

| Path | Hardware | Clock and lanes | Evidence boundary |
|---|---|---:|---|
| Legacy Testnet4 | FK33 FK0957 | 5 lanes at 200 MHz | 114 accepted, 0 rejected; preserved unchanged |
| PyBLOCK mainnet | 8 FK33s | 6 lanes at 195 MHz | physical difficulty-1 share proof, live Carousel work on every device |
| PyBLOCK mainnet | JCM33 FPGA A and B | 6 lanes at 195 MHz per FPGA | dual-lane physical soak plus live paired Carousel work |
| Template supplier | Bitcoin Knots 29.4.1 rc4 | mainnet BLAKE2b node | spam-free template accepted by PyBLOCK as `zombiekilla25` |

The default mining endpoint is the dedicated `zombiekilla25` supplier pool:

```text
Pool:      stratum+tcp://pool.pyblock.xyz:21020
Username:  YOUR_BTC_ADDRESS.worker
Password:  x
```

### Bitstream developer fee

Official FK33 v8 and JCM33 v2 mining clients implement a disclosed **1%
developer fee**. The clients implement this separately from the pool's supplier
split by assigning exactly one of every 100 work dispatches to a second Stratum
session, while that session is healthy, authorized as:

```text
bc1qe77h4ddu6cctl4zgxhy4wa6cf2z0gpsxw9dkvu.devfee
```

The remaining 99 dispatches mine to the user's configured payout address. If
the developer session is unavailable, the clients fail open to user mining and
never recover missed fee slots later. PyBLOCK's supplier and pool shares are
separate pool-side allocations; they are not the bitstream developer fee.
Third-party clients that do not implement the second-session rotation do not
enforce this fee.

On 2026-08-31, two FK33 v8 processes and one dual-JCM33 v2 process each held two
established connections to `pool.pyblock.xyz:21020`, proving separate primary
and developer Stratum sessions. Offline tests proved deterministic 1-in-100
scheduling, exact translator-compatible handshake IDs, job/session routing,
and fail-open behavior. This does not claim that a developer share had already
been accepted at that snapshot.

The 2026-08-31 validation snapshot used the earlier PyBLOCK Carousel endpoint
at `pool.pyblock.xyz:30110`, which negotiated share difficulty 16,384. At that
snapshot all ten FPGA devices were receiving and rolling work with zero
rejected, invalid, or stale shares, but no difficulty-16,384 share had yet been
found. This is a live work-dispatch result, not a pool-accepted Carousel-share
qualification or a share-derived hashrate measurement.

## Mainnet deployment

- [PyBLOCK Carousel mainnet guide](docs/PYBLOCK_CAROUSEL_MAINNET.md)
- [JCM33 dual-FPGA guide](docs/JCM33_BLAKE2B_MAINNET.md)
- [Template supplier guide](docs/TEMPLATE_SUPPLIER.md)
- [PyBLOCK runtime architecture and artifact record](software/pyblock-carousel/README.md)
- [2026-08-31 validation snapshot](evidence/mainnet/validation-20260831.json)

The six-lane FK33 and JCM33 images share the same BLAKE2b algorithm but use
different physical transports and are not interchangeable. Authenticate the
exact bitstream, adapter, translator, client, bridge, and serial mapping before
programming hardware.

## Original Testnet4 reference

The original five-lane, 200 MHz Profile-0 FK0957 release remains intact under:

- `hardware/` — RTL, constraints, testbench, and Vivado build flow;
- `software/continuous-v3/` — physically exercised localhost Testnet4 client;
- `docs/BSCAN_PROTOCOL.txt` — 121-byte job / 45-byte result transport;
- `evidence/hardware/` and `evidence/implementation/` — original qualification;
- `RELEASE_ASSETS_SHA256.txt` and `REPOSITORY_SHA256.txt` — version-1 hashes.

The original qualified release asset is
`fk33_blake2b_profile0_5lane_200_testnet4_v1_33880a23.bit`, SHA-256
`33880a2339d8b03db044f74e8258353f9c8f1e9832e74b52efccd18e2328872c`.

## Safety and qualification boundary

Programming replaces the active FPGA configuration. Stop other JTAG, Vivado,
hardware-server, USB/IP, and SQRL owners before loading an image. Keep an
authenticated restore image available. The published deployment does not
change voltage.

Pool authorization and rising job/roll counters prove connectivity and work
dispatch; they do not prove an accepted high-difficulty share. Do not report a
Carousel hashrate until accepted shares provide a statistically useful sample.
See [SECURITY.md](SECURITY.md) before operating on mainnet.

## Licensing

No blanket license grant is made. Review [NOTICE.md](NOTICE.md) before
redistributing source, bitstreams, bridge binaries, or third-party components.
