# PyBLOCK BLAKE2b template supplier

A synchronized Bitcoin Knots 29.4.1 rc4 mainnet node publishes spam-free
BLAKE2b block templates to PyBLOCK under the public name `zombiekilla25`.
Private node addresses, RPC ports, data paths, service names, and credentials
are deliberately excluded.

## Published boundary

```text
Software:   Bitcoin Knots 29.4.1 / 20260508rc4
Chain:      main
Policy:     datacarrier=0
Supplier:   zombiekilla25
```

The initial accepted registration reported `gate=passed`, 50 transactions
validated, and version `0xa0000000`. The supplier republishes on new blocks and
periodically for mempool freshness.

Carousel miners rotate across independent supplier templates. If a miner finds
a block using a supplier template, payout behavior is governed by PyBLOCK's
then-current public policy. This repository records an observed operating path;
it does not control or guarantee availability, rewards, fees, or payouts.

Before publication, request a BLAKE2b block template and verify it contains no
OP_RETURN transaction under the configured policy. Never publish RPC
credentials, cookies, wallet files, private keys, internal endpoints, or node
configuration contents.

