# FK33 FK0957 continuous BLAKE2b miner v3

This release turns the qualified FK0957 BLAKE2b/DATUM path into a supervised,
long-running Testnet4 miner. Every release file has a new v3 filename and all
files are contained in this single folder.

Target identity:

- USB serial: `153300000957`
- FPGA DNA: `4000000001295d813c80a445`
- BLAKE2b image: node-consistent five-lane 200 MHz build
- Nominal datapath capacity: 1.000 GH/s
- Qualified 30-minute result: 380/380 accepted at 0.907 GH/s

## Service controls

The service remains active after the terminal closes.

```bash
bash ./fk33_fk0957_continuous_service_v3.sh start
bash ./fk33_fk0957_continuous_service_v3.sh status
bash ./fk33_fk0957_continuous_service_v3.sh follow
bash ./fk33_fk0957_continuous_service_v3.sh stop
```

- `start` authenticates sudo, launches the supervisor in the background, and
  waits until the durable state reports `MINING`.
- `status` displays the active process plus current accepted/rejected counts,
  difficulty, all-time and rolling hashrate, session count, reconnect count,
  current job, last-share age, and any error.
- `follow` watches the active run's complete console. Pressing `Ctrl+C` while
  following stops only `tail`; it does not stop the miner.
- `stop` signals the exact authenticated supervisor PID and waits for DATUM
  shutdown and authenticated FJAR restoration. It reports success only after a
  clean restoration record exists.

Do not force-kill the supervisor. Use `stop` so the restoration trap runs.

## Continuous safety and recovery behavior

The supervisor and engine enforce the following:

- only FK0957 may be connected as an SQRL FK target;
- any existing FPGA/JTAG/DATUM owner or SQRL bridge causes a fail-closed abort;
- JCM33 bridge variants are excluded and never stopped by this service;
- DATUM Stratum and API listeners are restricted to localhost;
- DATUM remote-pool mode is disabled;
- every hardware nonce is independently reconstructed and hashed before submit;
- a DATUM rejection, invalid hardware frame, digest mismatch, or target mismatch
  is fatal and immediately enters shutdown/restoration;
- socket failures are retried with exponential backoff inside a five-minute
  recovery window;
- a five-minute accepted-share watchdog recovers a silent session;
- after warmup, a rolling 30-minute hashrate outside 0.50–1.50 GH/s activates
  recovery and ultimately fails closed if health is not restored;
- the process-level watchdog stops the run if DATUM or its localhost listeners
  disappear for 15 seconds;
- extranonce2 rolls every five seconds, after every share, and on every live
  DATUM notify;
- only one submit is outstanding at a time and every response is recorded;
- `SIGINT`, `SIGTERM`, startup failure, engine failure, and watchdog failure all
  use the same cleanup and authenticated FJAR restoration path.

## Persistent records

Each start creates a uniquely timestamped directory beneath:

```text
$HOME/bip110_blake2b_stack/fk33-blake2b-fk0957-continuous-v3-<UTC timestamp>
```

Important files include:

- `miner-state.json` — atomically replaced durable status every ten seconds and
  after every accepted share;
- `accepted-shares.jsonl` — one durable record per physical-FPGA submission;
- `stratum-events.jsonl` — complete inbound/outbound Stratum event stream;
- `session-events.jsonl` — session starts, readiness, recovery, and endings;
- `continuous-engine.log`, DATUM logs, bridge logs, and USB identity;
- `service-final.json` — final outcome and restoration state;
- `final-artifacts.sha256` — sealed run artifacts after shutdown.

The three JSONL streams rotate at 64 MiB with eight retained backups, bounding
their storage while preserving substantial history. Aggregate counters remain
in `miner-state.json` for the life of that run.

## Network scope

This is continuous real Testnet4 mining. Shares are sent to the qualified
localhost DATUM gateway, which reads the synchronized local RC2 Testnet4 node.
No remote pool is configured or contacted. A share that meets the Testnet4
network target may cause DATUM to submit a Testnet4 block to the local node.

The service does not change wallet, voltage, fan, or fleet state. On controlled
stop or failure it loads the authenticated FJAR 500 MHz image, stops the bridge,
rebinds only interfaces released from FK0957, and leaves the board idle.

## Installation and first start

From the directory containing the downloaded archive:

```bash
set -Eeuo pipefail

ARCHIVE="fk33_blake2b_fk0957_continuous_v3_release.zip"
FOLDER="fk33_blake2b_fk0957_continuous_v3"

[[ ! -e "$FOLDER" ]] || {
    echo "ABORT: destination already exists: $FOLDER"
    exit 2
}

unzip -q "$ARCHIVE"
cd "$FOLDER"
sha256sum -c SHA256SUMS_CONTINUOUS_V3.txt

chmod 700 \
  fk33_b2b_profile0_adapter_node_order_v3.py \
  fk33_b2b_live_translator_node_order_v3.py \
  fk33_fk0957_continuous_engine_v3.py \
  fk33_fk0957_continuous_service_v3.sh

bash ./fk33_fk0957_continuous_service_v3.sh selftest
bash ./fk33_fk0957_continuous_service_v3.sh start
```

A successful start ends with:

```text
RESULT: FK33_BLAKE2B_CONTINUOUS_V3_STARTED
```

Then use `status`, `follow`, and `stop` from the same extracted folder.
