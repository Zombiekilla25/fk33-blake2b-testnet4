# FK33 BLAKE2b Testnet4 reference

Experimental Testnet4-only FPGA miner reference for the FK33. This repository
preserves the original five-lane, 200 MHz Profile-0 image physically qualified
on FK0957.

## Qualified image

- Release asset: `fk33_blake2b_profile0_5lane_200_testnet4_v1_33880a23.bit`
- SHA-256: `33880a2339d8b03db044f74e8258353f9c8f1e9832e74b52efccd18e2328872c`
- Device: SQRL FK33 / Xilinx Virtex UltraScale+ VU33P
- Datapath: five lanes at 200 MHz
- Nominal capacity: 1.000 GH/s
- Vivado implementation: setup WNS `+0.034 ns`, hold WHS `+0.009 ns`
- Routing errors: 0
- DRC errors: 0

## Physical qualification

The image passed an isolated hardware known-answer test and an authenticated
DATUM job replay on FK0957. A continuous localhost Testnet4 run subsequently
recorded 114 accepted shares, zero rejected shares, zero unknown submissions,
one session, and no reconnects. Its final estimated rate was approximately
0.89 GH/s.

The exact sanitized evidence and implementation reports are committed under
`evidence/`. Release-asset hashes are in `RELEASE_ASSETS_SHA256.txt`.

## Scope and safety

This is an experimental Testnet4 research release. It is not qualified for
mainnet, production mining, unattended fleet deployment, or automatic voltage
or clock changes. Loading FPGA images can disrupt hardware operation. Keep a
known-good restore image available and target one explicitly identified board.

No RPC credentials, wallet files, mining addresses, DATUM configurations,
bridge binaries, or restoration images are included. The transport bridge and
restore image must be obtained and reviewed separately.

## What is included

- `hardware/`: exact RTL, constraints, simulation testbench, and build Tcl for
  the qualified five-lane lineage.
- `software/continuous-v3/`: the physically exercised localhost Testnet4
  continuous client release.
- `docs/BSCAN_PROTOCOL.txt`: exact 121-byte job / 45-byte result transport.
- `evidence/`: implementation and physical-test records.

## What is deliberately excluded

- the experimental six-lane build;
- the discarded target-comparator repair lineage;
- all wallet backups and RPC authentication material;
- DATUM pool mode and remote pool configuration;
- third-party SQRL bridge binaries and FJAR restore bitstreams.

## Rebuilding

The implementation Tcl targets Vivado 2026.1. Rebuilding can produce a
functionally equivalent image with a different binary hash. Do not replace the
qualified release asset without rerunning RTL simulation, timing, route, DRC,
known-answer, live job, and continuous share-acceptance gates.

## Licensing

No blanket license grant is made by this initial evidence release. Review
`NOTICE.md` before redistributing source, bitstreams, or third-party components.
