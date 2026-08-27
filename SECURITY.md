# Security and responsible testing

- Testnet4 only.
- Never publish RPC credentials, cookies, wallet backups, or private keys.
- Bind DATUM, Stratum, bridge, and control listeners to localhost unless an
  independently reviewed network policy requires otherwise.
- Identify one FPGA by USB serial and DNA before loading an image.
- Keep a separately authenticated restore image available.
- Do not change voltage, fan control, or fleet state as part of qualification.
- Stop on any serial, DNA, checksum, timing, route, DRC, or share-validation
  mismatch.

Report potential security problems privately to the repository owner before
opening a public issue containing sensitive operational details.
