# JCM33 dual-FPGA BLAKE2b mainnet path

The JCM33 carrier exposes two VU33P devices behind one JTAG chain. The
production design uses a single serialized XVC controller so independent
clients cannot race the chain. Private carrier addresses, local control ports,
service names, and telemetry are deliberately excluded from this public record.

## Authenticated identity

```text
XVC protocol:  xvcServer_v1.0:4096
Devices:       2
IR length:     12 bits
Worker model:  YOUR_BTC_ADDRESS.jcm33
```

The image is `jcm33_blake2b_sixlane195_v1.bit`, SHA-256
`942ebd3f3fce3a6ac192c12c895d0702b160ad35d8a0e6966b1239a4466d4169`.
It is distinct from the FK33 bitstream.

## Physical validation

- FPGA A returned an adapter-decodable share through the XVC path.
- FPGA B independently returned an adapter-decodable share through the XVC
  path.
- The paced dual-lane soak completed 120/120 verified rounds on both lanes.
- Pool access and `mining.submit` were disabled during those physical tests.
- Each test restored the authenticated JCM33 587.5 MHz BitcoinIII image.

## Mainnet runtime

One supervised bridge owns the JTAG/XVC session and one supervised dual-lane
client owns Stratum. The client authenticates the two-device chain, completes
the Carousel handshake, and dispatches a unique tag to each lane for every
initial, nonce-space-roll, or Stratum-update event.

The first observed Carousel progress record reported difficulty 16,384 with
zero accepted, rejected, invalid, and stale shares. That is expected in the
first minute and proves no accepted-share qualification by itself.

The original bridge exposed an invalid negative temperature sentinel for an
unsupported sensor field. Do not use unsupported fields for thermal protection;
use independently validated board and VRM telemetry.

