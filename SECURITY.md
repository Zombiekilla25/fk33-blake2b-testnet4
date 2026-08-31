# Security and responsible operation

- Never publish RPC credentials, cookies, wallet backups, seed phrases,
  passphrases, private keys, or exchange credentials.
- A public payout address and worker suffix are sufficient for Stratum.
- Bind RPC, XVC, bridge, DATUM, and control listeners to localhost or an
  independently reviewed isolated network whenever possible.
- The SQRL bridge may listen on all interfaces; firewall its port range.
- Authenticate the hardware serial/DNA, bitstream, bridge, adapter, translator,
  client, and serial-to-port map before programming.
- The FK33 and JCM33 bitstreams use different physical transports and are not
  interchangeable.
- Stop other Vivado, hardware-server, USB/IP, JTAG, and SQRL owners before
  taking control of a device.
- Keep a separately authenticated restore image and a documented stop path.
- Do not change voltage as part of software deployment or qualification.
- Treat persistent checksum, identity, digest, target, invalid-frame,
  stale-frame, rejected-share, or control-path errors as a stop condition.
- Pool authorization and increasing job counters do not prove accepted shares
  or measured hashrate.
- Mainnet mining can submit a valid block and can create financial or policy
  consequences. Review every endpoint and payout address before launch.

Report potential security problems privately to the repository owner before
opening a public issue containing sensitive operational details.

