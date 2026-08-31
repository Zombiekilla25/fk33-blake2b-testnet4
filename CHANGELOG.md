# Changelog

## 2.1.0-beta - 2026-08-31

- Make the dedicated `zombiekilla25` supplier endpoint at
  `pool.pyblock.xyz:21020` the documented default.
- Publish FK33 client v8 and JCM33 dual client v2 with a disclosed client-level
  1% developer fee, separate from pool-side supplier and pool allocations.
- Use deterministic one-in-100 work dispatch with fail-open user mining and no
  catch-up after developer-session outages.
- Add exact-production-translator handshake tests and separate user/developer
  response accounting.
- Record live dual-session verification on two FK33 clients and one JCM33
  client without claiming an accepted developer share.

## 2.0.0-beta - 2026-08-31

- Document the six-lane 195 MHz BLAKE2b mainnet deployment across eight FK33s
  and both FPGAs on one JCM33 carrier.
- Record the PyBLOCK Carousel endpoint, difficulty-16,384 operating behavior,
  service layout, wallet worker naming, and statistical share expectations.
- Publish a sanitized runtime architecture and checksum record without private
  LAN addresses, device serials, local paths, or host-specific launch scripts.
- Record the physical FK33 difficulty-1 proof and the JCM33 dual-lane soak.
- Document the `zombiekilla25` spam-free BLAKE2b template supplier.
- Preserve the original FK0957 five-lane Testnet4 release unchanged.

Qualification boundary: live Carousel work was observed on all ten FPGA
devices with zero rejected/invalid/stale shares at the snapshot, but no
difficulty-16,384 Carousel share had yet been accepted.

## 1.0.0 - 2026-08-27

- Publish the qualified FK0957 five-lane 200 MHz Testnet4 reference.
