#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SERIAL="153300000957"
DNA="4000000001295d813c80a445"
PORT=23000
STRATUM_PORT=23339
API_PORT=17152

ACTION=${1:-help}
if (( $# > 1 )); then
    echo "Usage: $(basename "$0") {start|status|follow|stop|run|selftest}" >&2
    exit 2
fi

RELEASE_DIR=$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd -P
)
SELF="$RELEASE_DIR/$(basename "${BASH_SOURCE[0]}")"

STACK="$HOME/bip110_blake2b_stack"
CONTROL="$STACK/fk33-blake2b-fk0957-continuous-v3-control"
PIDFILE="$CONTROL/supervisor.pid"
ACTIVE_RUN_FILE="$CONTROL/active-run.txt"
LAUNCH_LOG="$CONTROL/service-launch.log"

BUILD="$HOME/fk_blake2b_lab/blake2b-profile0-fk33-bscan-200-build-20260826T181650Z"
OUT="$BUILD/output"
BLAKE_BIT="$OUT/fk33_blake2b_profile0_5lane_200.bit"
BLAKE_DCP="$OUT/fk33_blake2b_profile0_5lane_200_routed.dcp"
TIMING_REPORT="$OUT/timing_routed.rpt"
ROUTE_REPORT="$OUT/route_status.rpt"
DRC_REPORT="$OUT/drc_routed.rpt"
UTIL_REPORT="$OUT/utilization_routed.rpt"
IMPLEMENTATION_LOG="$BUILD/implementation.log"
SOURCE_SEAL="$BUILD/source-seal.sha256"
PROTOCOL="$BUILD/BSCAN_PROTOCOL.txt"
RESTORE_BIT="$HOME/Downloads/fjar_bscan_500_integration_v1/hardware/output/fk33_fjar_bscan_500.bit"
BRIDGE="$HOME/FJARCODE/public_releases/fk33-fjar-miner-0.1.0-beta/research/sha3t_ii1/full_500/fk0957_raw_bridge/sqrl_bridge_rawjtag_coe"
LIBS="$HOME/jc33_compat_libs"

ADAPTER="$RELEASE_DIR/fk33_b2b_profile0_adapter_node_order_v3.py"
TRANSLATOR="$RELEASE_DIR/fk33_b2b_live_translator_node_order_v3.py"
ENGINE="$RELEASE_DIR/fk33_fk0957_continuous_engine_v3.py"

DATUM_STAGE="$STACK/datum-rc2-software-stage-20260827T004354Z"
DATUM="$DATUM_STAGE/build-qualified/datum_gateway"
BASE_CONFIG="$DATUM_STAGE/runtime/datum_gateway_config.json"

BLAKE_SHA="33880a2339d8b03db044f74e8258353f9c8f1e9832e74b52efccd18e2328872c"
BLAKE_DCP_SHA="85992c1c75e91e4f8e018ad5e3f91595f4f270469a7dfc169f3c8be82fdbf14c"
TIMING_SHA="29e9994b0e2761acb247716caaeba5c652874a9c13cf5bf65f6faa84cd4f035f"
ROUTE_SHA="8eabe29a048ddad3597b329799537ef0307a8fa238f900a288c812f86b31cfc5"
DRC_SHA="909a5d019a551af7db13947d0b36c6562bbccb723d637655b37bc4b124d5a938"
UTIL_SHA="09616999b8c4a304a52675e51015081dde41b9a786ea9a813668db0a3904f1be"
IMPLEMENTATION_LOG_SHA="337e1b9554c8164b5e09e187a5e2bf7bc80cf41bb0f6517b703f710e4bb6b44e"
SOURCE_SEAL_SHA="bc7a253a2caac67f2a0edca6e21965a438458caa91ea49c64781bcf3198e711b"
PROTOCOL_SHA="ab0db4db3e49db874e8ee187b6b955266a0f901125edb82b132e398b1505c22d"
RESTORE_SHA="efe740723b4ef4d93b29339cdeea32416495aabea8f78cb15f3456c44a354ecb"
BRIDGE_SHA="8c7230f0bf586e9297dc0e568bd19278aeeb7cff8dbb3dde150811f11393218a"
ADAPTER_SHA="5bc0982b893cea747514c9005805df9b0d4f380fb94ca733a3eada378e73a3f7"
TRANSLATOR_SHA="f63f8ce38bf2a774646837e66fe029f3b05d52c6a78c11b536485935eee36d1b"
ENGINE_SHA="63318aaccf4370a280e687e0fbcf63905a5148f799eed3b8041bdba2f8ac01ba"
DATUM_SHA="bab04ba8902589c6ad1286fb9ad0c5a99fb9ba9d460b244702608c8a6c488117"
BASE_CONFIG_SHA="57f8f755a16431a2b7ce767b43a057670220e0410baae8cf2815043264dfd0d0"

listener_exists() {
    local check_port=$1
    python3 - "$check_port" <<'PY'
from pathlib import Path
import sys

port = int(sys.argv[1])
needle = f"{port:04X}"
for name in ("/proc/net/tcp", "/proc/net/tcp6"):
    path = Path(name)
    if not path.is_file():
        continue
    for line in path.read_text(errors="replace").splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 4 and fields[1].rsplit(":", 1)[-1].upper() == needle and fields[3] == "0A":
            raise SystemExit(0)
raise SystemExit(1)
PY
}

read_supervisor_pid() {
    local value=""
    [[ -r "$PIDFILE" ]] || return 1
    value=$(<"$PIDFILE")
    [[ "$value" =~ ^[1-9][0-9]*$ ]] || return 1
    printf '%s\n' "$value"
}

supervisor_pid_is_valid() {
    local pid=$1
    local command_line=""
    kill -0 "$pid" 2>/dev/null || return 1
    [[ -r "/proc/$pid/cmdline" ]] || return 1
    command_line=$(tr '\0' ' ' <"/proc/$pid/cmdline")
    [[ "$command_line" == *"$SELF run"* ]]
}

atomic_control_write() {
    local pathname=$1
    local value=$2
    local temporary="$pathname.tmp-$$"
    printf '%s\n' "$value" >"$temporary"
    chmod 600 "$temporary"
    mv -f -- "$temporary" "$pathname"
}

show_usage() {
    cat <<EOF
Usage: $(basename "$0") {start|status|follow|stop|run|selftest}

  start     authenticate sudo, launch the miner in the background, and wait ready
  status    show supervisor state and the latest durable mining statistics
  follow    follow the active run's complete console log
  stop      request controlled shutdown and wait for authenticated FJAR restore
  selftest  run offline release checks only
  run       internal foreground supervisor used by start
EOF
}

status_action() {
    local pid=""
    local run=""
    local state=""
    if pid=$(read_supervisor_pid) && supervisor_pid_is_valid "$pid"; then
        echo "STATUS: RUNNING"
        ps -p "$pid" -o pid,etime,%cpu,%mem,stat,cmd
    else
        echo "STATUS: STOPPED"
    fi

    if [[ -r "$ACTIVE_RUN_FILE" ]]; then
        run=$(<"$ACTIVE_RUN_FILE")
        echo "run: $run"
        state="$run/miner-state.json"
        if [[ -s "$state" ]]; then
            jq '{
              status,
              wall_runtime_seconds,
              active_mining_seconds,
              accepted_shares,
              rejected_shares,
              unknown_submissions,
              acceptance_ratio,
              accepted_difficulty_units,
              estimated_hashrate_ghs,
              rolling_30m_hashrate_ghs,
              network_target_shares,
              sessions_started,
              reconnects,
              current_difficulty,
              current_job_id,
              last_accepted_age_seconds,
              last_error,
              updated_unix
            }' "$state"
        else
            echo "NOTICE: miner state is not available yet"
        fi
    fi
}

start_action() {
    local pid=""
    local run=""
    mkdir -p "$CONTROL"
    chmod 700 "$CONTROL"

    if pid=$(read_supervisor_pid) && supervisor_pid_is_valid "$pid"; then
        echo "ABORT: continuous miner is already running as PID $pid"
        status_action
        exit 2
    fi

    sudo -v
    : >>"$LAUNCH_LOG"
    chmod 600 "$LAUNCH_LOG"

    nohup env FK33_CONTINUOUS_BACKGROUND=1 \
      bash "$SELF" run >>"$LAUNCH_LOG" 2>&1 </dev/null &
    pid=$!
    atomic_control_write "$PIDFILE" "$pid"

    echo "Starting FK0957 continuous BLAKE2b miner as PID $pid..."
    for _ in {1..240}; do
        if ! supervisor_pid_is_valid "$pid"; then
            echo "ABORT: supervisor exited during startup"
            tail -160 "$LAUNCH_LOG" || true
            exit 2
        fi
        if [[ -r "$ACTIVE_RUN_FILE" ]]; then
            run=$(<"$ACTIVE_RUN_FILE")
            if [[ -s "$run/miner-state.json" ]] &&
               jq -e '.status == "MINING"' "$run/miner-state.json" >/dev/null 2>&1; then
                echo "RESULT: FK33_BLAKE2B_CONTINUOUS_V3_STARTED"
                echo "PID: $pid"
                echo "run: $run"
                echo
                echo "Status: bash \"$SELF\" status"
                echo "Follow: bash \"$SELF\" follow"
                echo "Stop:   bash \"$SELF\" stop"
                return 0
            fi
        fi
        sleep 1
    done

    echo "ABORT: miner did not reach MINING state within 240 seconds"
    kill -TERM "$pid" 2>/dev/null || true
    tail -160 "$LAUNCH_LOG" || true
    exit 2
}

follow_action() {
    local run=""
    if [[ -r "$ACTIVE_RUN_FILE" ]]; then
        run=$(<"$ACTIVE_RUN_FILE")
    fi
    if [[ -n "$run" && -f "$run/service-console.log" ]]; then
        tail -F "$run/service-console.log"
    elif [[ -f "$LAUNCH_LOG" ]]; then
        tail -F "$LAUNCH_LOG"
    else
        echo "ABORT: no continuous miner log exists"
        exit 2
    fi
}

stop_action() {
    local pid="" run="" final=""
    if ! pid=$(read_supervisor_pid) || ! supervisor_pid_is_valid "$pid"; then
        echo "PASS: continuous miner is already stopped"
        status_action
        return 0
    fi

    echo "Requesting controlled stop from supervisor PID $pid..."
    kill -TERM "$pid"
    for _ in {1..300}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            if [[ -r "$ACTIVE_RUN_FILE" ]]; then
                run=$(<"$ACTIVE_RUN_FILE")
                final="$run/service-final.json"
            fi
            if [[ -s "$final" ]] &&
               jq -e '.outcome == "STOPPED_CLEANLY" and .restore_ok == true' \
                 "$final" >/dev/null 2>&1; then
                echo "RESULT: FK33_BLAKE2B_CONTINUOUS_V3_STOPPED_CLEANLY"
            else
                echo "CRITICAL: supervisor exited without a clean authenticated restoration record"
                [[ -n "$run" ]] && echo "inspect: $run"
                exit 3
            fi
            status_action
            return 0
        fi
        sleep 1
    done
    echo "CRITICAL: supervisor did not stop within 300 seconds"
    echo "Do not force-kill it; inspect with: bash \"$SELF\" follow"
    exit 3
}

selftest_action() {
    local test_dir=""
    test_dir=$(mktemp -d)

    python3 -m py_compile "$ADAPTER" "$TRANSLATOR" "$ENGINE"
    python3 "$ENGINE" \
      --adapter "$ADAPTER" \
      --translator "$TRANSLATOR" \
      selftest \
      --directory "$test_dir"
    find "$test_dir" -depth -type f -delete 2>/dev/null || true
    rmdir "$test_dir" 2>/dev/null || true
    echo "RESULT: FK33_BLAKE2B_CONTINUOUS_V3_RELEASE_SELFTEST_PASS"
}

case "$ACTION" in
    start) start_action; exit 0 ;;
    status) status_action; exit 0 ;;
    follow) follow_action; exit 0 ;;
    stop) stop_action; exit 0 ;;
    selftest) selftest_action; exit 0 ;;
    -h|--help|help) show_usage; exit 0 ;;
    run) ;;
    *) echo "ABORT: unsupported action: $ACTION" >&2; show_usage >&2; exit 2 ;;
esac

# Everything below this line runs only in the long-lived foreground supervisor.

STAMP=$(date -u '+%Y%m%dT%H%M%SZ')
RUN="$STACK/fk33-blake2b-fk0957-continuous-v3-$STAMP"
SERVICE_CONSOLE="$RUN/service-console.log"
DATUM_CONFIG="$RUN/datum_gateway_config.json"
DATUM_STDOUT="$RUN/datum-stdout.log"
DATUM_LOG="$RUN/datum.log"
ENGINE_LOG="$RUN/continuous-engine.log"
STATE_JSON="$RUN/miner-state.json"
SHARES_JSONL="$RUN/accepted-shares.jsonl"
STRATUM_JSONL="$RUN/stratum-events.jsonl"
SESSIONS_JSONL="$RUN/session-events.jsonl"
CANARY_BLOG="$RUN/blake2b-bridge.log"
CANARY_SLOG="$RUN/blake2b-bridge-stdout.log"
RESTORE_BLOG="$RUN/restore-bridge.log"
RESTORE_SLOG="$RUN/restore-bridge-stdout.log"
USB_LOG="$RUN/usb-identity.log"
SELFTEST_LOG="$RUN/release-selftest.log"
SEAL="$RUN/final-artifacts.sha256"
FINAL_JSON="$RUN/service-final.json"

BRIDGE_PID=""
DATUM_PID=""
ENGINE_PID=""
SUDO_KEEPALIVE_PID=""
USBDEV=""
UNBOUND=()
NEED_RESTORE=0
RESTORE_ATTEMPTED=0
RESTORE_OK=0
RESTORE_FAILED=0
INTERFACE_RESTORE_FAILED=0
USER_STOP=0
CLEANUP_RUNNING=0
MINER_STARTED=0

local_bridge_pids() {
    {
        pgrep -f -- "[s]qrl_bridge_rawjtag_coe -s ,$SERIAL -b $BLAKE_BIT -p $PORT " || true
        pgrep -f -- "[s]qrl_bridge_rawjtag_coe -s ,$SERIAL -b $RESTORE_BIT -p $PORT " || true
    } | sort -nu
}

stop_local_bridge() {
    local -a pids=()
    mapfile -t pids < <(local_bridge_pids)
    if (( ${#pids[@]} )); then
        sudo -n kill -TERM "${pids[@]}" 2>/dev/null || true
        sleep 3
    fi
    mapfile -t pids < <(local_bridge_pids)
    if (( ${#pids[@]} )); then
        sudo -n kill -KILL "${pids[@]}" 2>/dev/null || true
        sleep 1
    fi
    mapfile -t pids < <(local_bridge_pids)
    if (( ${#pids[@]} )); then
        echo "FAIL: owned bridge processes remain: ${pids[*]}"
        return 1
    fi
    if [[ -n "$BRIDGE_PID" ]]; then
        wait "$BRIDGE_PID" 2>/dev/null || true
    fi
    BRIDGE_PID=""
}

release_fk0957_interfaces() {
    local iface pathname driver serial_file serial_value usb_candidate
    local -a usb_candidates=()

    for serial_file in /sys/bus/usb/devices/*/serial; do
        [[ -r "$serial_file" ]] || continue
        serial_value=$(<"$serial_file")
        if [[ "$serial_value" == "$SERIAL" ||
              "$serial_value" == "${SERIAL}A" ||
              "$serial_value" == "${SERIAL}B" ]]; then
            usb_candidate=$(basename "$(dirname "$serial_file")")
            usb_candidates+=("${usb_candidate%%:*}")
        fi
    done
    mapfile -t usb_candidates < <(
        printf '%s\n' "${usb_candidates[@]}" | sed '/^$/d' | sort -u
    )
    (( ${#usb_candidates[@]} == 1 )) || {
        echo "ABORT: expected exactly one USB device for serial $SERIAL; found ${#usb_candidates[@]}"
        return 1
    }
    USBDEV=${usb_candidates[0]}
    echo "USB device: $USBDEV" | tee -a "$USB_LOG"

    shopt -s nullglob
    local -a interfaces=(/sys/bus/usb/devices/"$USBDEV":1.*)
    shopt -u nullglob
    (( ${#interfaces[@]} )) || {
        echo "ABORT: no FTDI interfaces were found for $USBDEV"
        return 1
    }
    for pathname in "${interfaces[@]}"; do
        iface=$(basename "$pathname")
        if [[ -L "$pathname/driver" ]]; then
            driver=$(basename "$(readlink -f "$pathname/driver")")
            [[ "$driver" == "ftdi_sio" ]] || {
                echo "ABORT: unexpected driver on $iface: $driver"
                return 1
            }
            printf '%s\n' "$iface" |
              sudo -n tee /sys/bus/usb/drivers/ftdi_sio/unbind >/dev/null
            UNBOUND+=("$iface")
            echo "unbound: $iface" | tee -a "$USB_LOG"
        else
            echo "already free: $iface" | tee -a "$USB_LOG"
        fi
    done
}

rebind_fk0957_interfaces() {
    local iface pathname failed=0
    for iface in "${UNBOUND[@]}"; do
        pathname="/sys/bus/usb/devices/$iface"
        if [[ ! -e "$pathname" ]]; then
            echo "FAIL: USB interface disappeared before rebind: $iface"
            failed=1
            continue
        fi
        if [[ ! -L "$pathname/driver" ]]; then
            printf '%s\n' "$iface" |
              sudo -n tee /sys/bus/usb/drivers/ftdi_sio/bind >/dev/null 2>&1 || {
                echo "FAIL: could not rebind FTDI interface: $iface"
                failed=1
                continue
            }
        fi
        if [[ ! -L "$pathname/driver" ]] ||
           [[ $(basename "$(readlink -f "$pathname/driver")") != "ftdi_sio" ]]; then
            echo "FAIL: FTDI interface was not restored: $iface"
            failed=1
        fi
    done
    return "$failed"
}

start_bridge() {
    local bitstream=$1 bridge_log=$2 stdout_log=$3
    : >"$bridge_log"
    : >"$stdout_log"
    nohup sudo -n env LD_LIBRARY_PATH="$LIBS" \
      "$BRIDGE" -s ",$SERIAL" -b "$bitstream" -p "$PORT" -t -f "$bridge_log" \
      >"$stdout_log" 2>&1 </dev/null &
    BRIDGE_PID=$!
}

wait_bridge_ready() {
    local bitstream=$1 bridge_log=$2 label=$3 loaded_count
    for _ in {1..90}; do
        if grep -Fq 'Board 0 Device 1' "$bridge_log" 2>/dev/null; then
            echo "ABORT: $label bridge discovered more than Device 0"
            return 1
        fi
        loaded_count=$(grep -Fc 'SQRL JTAG Board 0 Device 0 Bitstream Loaded' "$bridge_log" 2>/dev/null || true)
        if (( loaded_count == 1 )) &&
           grep -Fq "Board 0 Device 0 DNA: $DNA" "$bridge_log" &&
           grep -Fq 'Found 1 FPGAs on board 0' "$bridge_log" &&
           grep -Fq "Device with serial ${SERIAL}A matches filter $SERIAL" "$bridge_log" &&
           grep -Fq "Using Bitstream $bitstream for SQRL JTAG Board 0 Device 0" "$bridge_log" &&
           listener_exists "$PORT"; then
            return 0
        fi
        if [[ -n "$BRIDGE_PID" ]] && ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
            echo "ABORT: $label bridge exited before becoming ready"
            return 1
        fi
        sleep 2
    done
    echo "ABORT: $label bridge did not become ready within 180 seconds"
    return 1
}

restore_fjar_image() {
    RESTORE_ATTEMPTED=1
    echo
    echo "===== AUTOMATICALLY RESTORE PROVEN FJAR IMAGE ====="
    stop_local_bridge || {
        RESTORE_FAILED=1
        return 1
    }
    start_bridge "$RESTORE_BIT" "$RESTORE_BLOG" "$RESTORE_SLOG"
    if ! wait_bridge_ready "$RESTORE_BIT" "$RESTORE_BLOG" restore; then
        RESTORE_FAILED=1
        tail -100 "$RESTORE_BLOG" "$RESTORE_SLOG" 2>/dev/null || true
        return 1
    fi
    echo "PASS: proven FJAR 500 MHz image loaded on serial $SERIAL"
    stop_local_bridge || {
        RESTORE_FAILED=1
        return 1
    }
    if local_bridge_pids | grep -q . || listener_exists "$PORT"; then
        RESTORE_FAILED=1
        echo "FAIL: restore bridge did not stop cleanly"
        return 1
    fi
    RESTORE_OK=1
    NEED_RESTORE=0
    echo "PASS: board is restored and idle"
}

stop_engine() {
    local status=0
    if [[ -n "$ENGINE_PID" ]] && kill -0 "$ENGINE_PID" 2>/dev/null; then
        kill -TERM "$ENGINE_PID" 2>/dev/null || true
        for _ in {1..180}; do
            kill -0 "$ENGINE_PID" 2>/dev/null || break
            sleep 0.25
        done
        if kill -0 "$ENGINE_PID" 2>/dev/null; then
            echo "FAIL: continuous engine did not stop within 45 seconds"
            kill -KILL "$ENGINE_PID" 2>/dev/null || true
            status=1
        fi
    fi
    if [[ -n "$ENGINE_PID" ]]; then
        wait "$ENGINE_PID" 2>/dev/null || true
    fi
    ENGINE_PID=""
    return "$status"
}

stop_datum() {
    local status=0
    if [[ -n "$DATUM_PID" ]] && kill -0 "$DATUM_PID" 2>/dev/null; then
        kill -TERM "$DATUM_PID" 2>/dev/null || true
        if wait "$DATUM_PID"; then status=0; else status=$?; fi
        if [[ "$status" -ne 0 && "$status" -ne 143 ]]; then
            echo "FAIL: DATUM returned unexpected stop status: $status"
            return 1
        fi
    elif [[ -n "$DATUM_PID" ]]; then
        wait "$DATUM_PID" 2>/dev/null || true
    fi
    DATUM_PID=""
    for _ in {1..40}; do
        if ! listener_exists "$STRATUM_PORT" && ! listener_exists "$API_PORT"; then
            return 0
        fi
        sleep 0.25
    done
    echo "FAIL: a localhost DATUM listener remained after shutdown"
    return 1
}

seal_run() {
    local -a files=()
    [[ -d "$RUN" ]] || return 0
    mapfile -d '' -t files < <(
        find "$RUN" -maxdepth 1 -type f \
          ! -name "$(basename "$SEAL")" \
          ! -name "$(basename "$SERVICE_CONSOLE")" \
          -print0 | sort -z
    )
    if (( ${#files[@]} )); then
        sha256sum "${files[@]}" >"$SEAL"
        chmod 600 "$SEAL"
    fi
}

cleanup() {
    local status=$? cleanup_status=0 outcome="ABORTED_SAFE"
    trap - EXIT
    trap '' INT TERM
    set +e
    if (( CLEANUP_RUNNING )); then exit "$status"; fi
    CLEANUP_RUNNING=1

    stop_engine || cleanup_status=1
    stop_datum || cleanup_status=1
    if (( NEED_RESTORE == 1 && RESTORE_ATTEMPTED == 0 )); then
        restore_fjar_image || cleanup_status=1
    elif (( NEED_RESTORE == 1 && RESTORE_ATTEMPTED == 1 && RESTORE_OK == 0 )); then
        RESTORE_FAILED=1
        cleanup_status=1
        stop_local_bridge || cleanup_status=1
    else
        stop_local_bridge || cleanup_status=1
    fi
    rebind_fk0957_interfaces || {
        INTERFACE_RESTORE_FAILED=1
        cleanup_status=1
    }
    if [[ -n "$SUDO_KEEPALIVE_PID" ]]; then
        kill "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
        wait "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
    fi

    if (( USER_STOP == 1 && cleanup_status == 0 && RESTORE_OK == 1 )); then
        outcome="STOPPED_CLEANLY"
        status=0
    elif (( cleanup_status != 0 )); then
        outcome="CRITICAL_CLEANUP_FAILURE"
        status=3
    fi

    jq -n \
      --arg schema 'fk33-blake2b-continuous-service-final-v3' \
      --arg outcome "$outcome" \
      --argjson exit_status "$status" \
      --argjson user_stop "$USER_STOP" \
      --argjson miner_started "$MINER_STARTED" \
      --argjson restore_ok "$RESTORE_OK" \
      --argjson restore_failed "$RESTORE_FAILED" \
      --argjson interface_restore_failed "$INTERFACE_RESTORE_FAILED" \
      --arg run "$RUN" \
      '{schema:$schema,outcome:$outcome,exit_status:$exit_status,user_stop:($user_stop==1),miner_started:($miner_started==1),restore_ok:($restore_ok==1),restore_failed:($restore_failed==1),interface_restore_failed:($interface_restore_failed==1),run:$run,finished_unix:now}' \
      >"$FINAL_JSON" 2>/dev/null || true
    chmod 600 "$FINAL_JSON" 2>/dev/null || true
    seal_run

    if [[ -r "$PIDFILE" ]] && [[ $(<"$PIDFILE") == "$$" ]]; then
        rm -f -- "$PIDFILE"
    fi

    echo
    if [[ "$outcome" == "STOPPED_CLEANLY" ]]; then
        echo "===== FINAL RESULT ====="
        echo "RESULT: FK33_BLAKE2B_CONTINUOUS_V3_STOPPED_CLEANLY"
        echo "PASS: continuous engine and DATUM were stopped"
        echo "PASS: proven FJAR 500 MHz image was restored and left idle"
        echo "run records: $RUN"
    elif [[ "$outcome" == "CRITICAL_CLEANUP_FAILURE" ]]; then
        echo "RESULT: FK33_BLAKE2B_CONTINUOUS_V3_CRITICAL_CLEANUP_FAILURE"
        echo "CRITICAL: do not start another miner until these records are inspected: $RUN"
    else
        echo "RESULT: FK33_BLAKE2B_CONTINUOUS_V3_ABORTED_SAFE"
        if (( RESTORE_OK == 1 )); then
            echo "PASS: proven FJAR image was restored despite the miner failure"
        fi
        echo "run records: $RUN"
    fi
    exit "$status"
}

handle_stop_signal() {
    USER_STOP=1
    exit 130
}

mkdir -p "$CONTROL" "$RUN"
chmod 700 "$CONTROL" "$RUN"
atomic_control_write "$PIDFILE" "$$"
atomic_control_write "$ACTIVE_RUN_FILE" "$RUN"
exec > >(tee -a "$SERVICE_CONSOLE") 2>&1

trap cleanup EXIT
trap handle_stop_signal INT TERM

echo "===== FK33 BLAKE2B FK0957 CONTINUOUS MINER V3 ====="
echo "serial: $SERIAL"
echo "DNA:    $DNA"
echo "PID:    $$"
echo "run:    $RUN"
echo
echo "This service mines continuously against localhost DATUM and the"
echo "synchronized RC2 Testnet4 node until the stop command is used."
echo "It performs host verification before every submission, reconnects"
echo "socket sessions inside a five-minute recovery window, checkpoints"
echo "statistics every ten seconds, and restores FJAR on every exit path."
echo "No remote pool is configured. A network-valid result may submit a"
echo "Testnet4 block to the local node."

echo
echo "===== PROCESS, PORT AND PRIVILEGE SAFETY GATE ====="
if pgrep -af '[d]atum_gateway|[t]eamredminer|[h]w_server|[v]ivado|[v]io_worker_|python3 .*[/]fjar_bridge[.]py'; then
    echo "ABORT: an FPGA/JTAG/DATUM owner is already active"
    exit 2
fi
if pgrep -af '[s]qrl_bridge'; then
    echo "ABORT: an SQRL bridge is already active"
    exit 2
fi
for check_port in "$PORT" "$STRATUM_PORT" "$API_PORT"; do
    if listener_exists "$check_port"; then
        echo "ABORT: TCP port $check_port is already listening"
        exit 2
    fi
done
if [[ ${FK33_CONTINUOUS_BACKGROUND:-0} == 1 ]]; then
    sudo -n true || {
        echo "ABORT: background start lost its authenticated sudo ticket"
        exit 2
    }
else
    sudo -v
fi
(
    while sudo -n true 2>/dev/null; do sleep 40; done
) &
SUDO_KEEPALIVE_PID=$!
echo "PASS: owners are stopped, ports are free and sudo is available"

echo
echo "===== AUTHENTICATE CONTINUOUS RELEASE AND QUALIFIED INPUTS ====="
declare -A EXPECTED_HASHES=(
    ["$BLAKE_BIT"]="$BLAKE_SHA"
    ["$BLAKE_DCP"]="$BLAKE_DCP_SHA"
    ["$TIMING_REPORT"]="$TIMING_SHA"
    ["$ROUTE_REPORT"]="$ROUTE_SHA"
    ["$DRC_REPORT"]="$DRC_SHA"
    ["$UTIL_REPORT"]="$UTIL_SHA"
    ["$IMPLEMENTATION_LOG"]="$IMPLEMENTATION_LOG_SHA"
    ["$SOURCE_SEAL"]="$SOURCE_SEAL_SHA"
    ["$PROTOCOL"]="$PROTOCOL_SHA"
    ["$RESTORE_BIT"]="$RESTORE_SHA"
    ["$BRIDGE"]="$BRIDGE_SHA"
    ["$ADAPTER"]="$ADAPTER_SHA"
    ["$TRANSLATOR"]="$TRANSLATOR_SHA"
    ["$ENGINE"]="$ENGINE_SHA"
    ["$DATUM"]="$DATUM_SHA"
    ["$BASE_CONFIG"]="$BASE_CONFIG_SHA"
)
for pathname in "${!EXPECTED_HASHES[@]}"; do
    [[ -s "$pathname" ]] || {
        echo "ABORT: required file is missing or empty: $pathname"
        exit 2
    }
    actual_hash=$(sha256sum "$pathname" | awk '{print $1}')
    [[ "$actual_hash" == "${EXPECTED_HASHES[$pathname]}" ]] || {
        echo "ABORT: SHA-256 mismatch: $pathname"
        echo "expected: ${EXPECTED_HASHES[$pathname]}"
        echo "actual:   $actual_hash"
        exit 2
    }
    echo "$actual_hash  $pathname"
done
[[ -x "$BRIDGE" && -x "$DATUM" ]] || {
    echo "ABORT: authenticated bridge or DATUM is not executable"
    exit 2
}
[[ $(basename "$BRIDGE") == sqrl_bridge_rawjtag_coe ]] || {
    echo "ABORT: an experimental bridge variant was selected"
    exit 2
}
grep -Fxq 'ROUTING STATUS: COMPLETE' "$IMPLEMENTATION_LOG"
grep -Fxq 'FINAL SETUP WNS: 0.034 ns' "$IMPLEMENTATION_LOG"
grep -Fxq 'FINAL HOLD WHS: 0.009 ns' "$IMPLEMENTATION_LOG"
grep -Fxq 'FINAL DRC ERROR COUNT: 0' "$IMPLEMENTATION_LOG"

python3 "$ENGINE" \
  --adapter "$ADAPTER" \
  --translator "$TRANSLATOR" \
  selftest --directory "$RUN/selftest" >"$SELFTEST_LOG"
grep -Fxq 'RESULT: FK33_BLAKE2B_CONTINUOUS_ENGINE_V3_SELFTEST_PASS' "$SELFTEST_LOG" || {
    echo "ABORT: continuous engine self-test failed"
    exit 2
}
env LD_LIBRARY_PATH="$LIBS" ldd "$BRIDGE" >"$RUN/bridge-ldd.log"
if grep -Fq 'not found' "$RUN/bridge-ldd.log"; then
    echo "ABORT: bridge has an unresolved shared-library dependency"
    exit 2
fi
python3 "$ADAPTER" selftest --bitstream "$BLAKE_BIT" --protocol "$PROTOCOL" >"$RUN/adapter-selftest.log"
grep -Fxq 'PASS: raw digest target orientation matches node uint256 reversal' "$RUN/adapter-selftest.log"
echo "PASS: continuous release and exact qualified stack authenticated"

echo
echo "===== CONFIRM AND RELEASE ONLY FK0957 ====="
: >"$USB_LOG"
mapfile -t FK_SERIALS < <(
    for serial_file in /sys/bus/usb/devices/*/serial; do
        [[ -r "$serial_file" ]] || continue
        serial_value=$(<"$serial_file")
        if [[ "$serial_value" =~ ^1533[0-9]{8}([AB])?$ ]]; then
            printf '%s\n' "${serial_value%[AB]}"
        fi
    done | sort -u
)
printf '%s\n' "${FK_SERIALS[@]}" | tee -a "$USB_LOG"
(( ${#FK_SERIALS[@]} == 1 )) && [[ "${FK_SERIALS[0]}" == "$SERIAL" ]] || {
    echo "ABORT: the only connected SQRL FK target is not serial $SERIAL"
    exit 2
}
release_fk0957_interfaces
echo "PASS: only FK0957 interfaces were released"

echo
echo "===== LOAD QUALIFIED BLAKE2B IMAGE ====="
NEED_RESTORE=1
start_bridge "$BLAKE_BIT" "$CANARY_BLOG" "$CANARY_SLOG"
wait_bridge_ready "$BLAKE_BIT" "$CANARY_BLOG" BLAKE2b || {
    tail -100 "$CANARY_BLOG" "$CANARY_SLOG" 2>/dev/null || true
    exit 2
}
echo "PASS: FK0957 BLAKE2b image and localhost transport are ready"

echo
echo "===== START LOCALHOST-ONLY QUALIFIED DATUM ====="
jq \
  --arg log_file "$DATUM_LOG" \
  --argjson stratum_port "$STRATUM_PORT" \
  --argjson api_port "$API_PORT" \
  '
    .stratum.listen_addr = "127.0.0.1" |
    .stratum.listen_port = $stratum_port |
    .stratum.vardiff_min = 1 |
    .api.listen_addr = "127.0.0.1" |
    .api.listen_port = $api_port |
    .api.modify_conf = false |
    .logger.log_file = $log_file |
    .logger.log_rotate_daily = true |
    .datum.pool_host = "" |
    .datum.pool_pubkey = "" |
    .datum.pooled_mining_only = false |
    .datum.pool_pass_workers = false |
    .datum.pool_pass_full_users = false
  ' "$BASE_CONFIG" >"$DATUM_CONFIG"
chmod 600 "$DATUM_CONFIG"
jq -e '
  .stratum.listen_addr == "127.0.0.1" and
  .stratum.listen_port == 23339 and
  .stratum.vardiff_min == 1 and
  .api.listen_addr == "127.0.0.1" and
  .api.listen_port == 17152 and
  .api.modify_conf == false and
  .datum.pool_host == "" and
  .datum.pool_pubkey == "" and
  .datum.pooled_mining_only == false and
  .datum.pool_pass_workers == false and
  .datum.pool_pass_full_users == false
' "$DATUM_CONFIG" >/dev/null || {
    echo "ABORT: DATUM configuration failed its localhost safety gate"
    exit 2
}
"$DATUM" -c "$DATUM_CONFIG" >"$DATUM_STDOUT" 2>&1 &
DATUM_PID=$!
DATUM_READY=0
for _ in {1..90}; do
    if ! kill -0 "$DATUM_PID" 2>/dev/null; then
        echo "ABORT: DATUM exited during startup"
        tail -120 "$DATUM_STDOUT" "$DATUM_LOG" 2>/dev/null || true
        exit 2
    fi
    if listener_exists "$STRATUM_PORT" && listener_exists "$API_PORT"; then
        DATUM_READY=1
        break
    fi
    sleep 1
done
(( DATUM_READY == 1 )) || {
    echo "ABORT: DATUM localhost listeners did not become ready"
    exit 2
}
echo "PASS: qualified DATUM is ready; remote pool mode is disabled"

echo
echo "===== START CONTINUOUS HARDWARE MINING ENGINE ====="
python3 "$ENGINE" \
  --adapter "$ADAPTER" \
  --translator "$TRANSLATOR" \
  run \
  --stratum-host 127.0.0.1 \
  --stratum-port "$STRATUM_PORT" \
  --fpga-host 127.0.0.1 \
  --fpga-port "$PORT" \
  --username fk33-continuous-v3 \
  --roll-seconds 5 \
  --share-watchdog-seconds 300 \
  --hashrate-watchdog-seconds 1800 \
  --minimum-rolling-ghs 0.50 \
  --maximum-rolling-ghs 1.50 \
  --recovery-window-seconds 300 \
  --progress-seconds 60 \
  --checkpoint-seconds 10 \
  --state-output "$STATE_JSON" \
  --shares-output "$SHARES_JSONL" \
  --stratum-events-output "$STRATUM_JSONL" \
  --session-events-output "$SESSIONS_JSONL" \
  > >(tee -a "$ENGINE_LOG") 2>&1 &
ENGINE_PID=$!

for _ in {1..180}; do
    if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
        echo "ABORT: continuous engine exited during startup"
        wait "$ENGINE_PID" || true
        exit 2
    fi
    if [[ -s "$STATE_JSON" ]] && jq -e '.status == "MINING"' "$STATE_JSON" >/dev/null 2>&1; then
        MINER_STARTED=1
        break
    fi
    sleep 1
done
(( MINER_STARTED == 1 )) || {
    echo "ABORT: continuous engine did not reach MINING state"
    exit 2
}

echo "RESULT: FK33_BLAKE2B_CONTINUOUS_V3_MINING"
echo "PASS: FK0957 is submitting only host-verified shares to localhost DATUM"
echo "state: $STATE_JSON"
echo "Stop safely with: bash \"$SELF\" stop"

missing_owner_since=0
while true; do
    if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
        if wait "$ENGINE_PID"; then engine_status=0; else engine_status=$?; fi
        ENGINE_PID=""
        echo "ABORT: continuous engine exited unexpectedly with status $engine_status"
        exit 2
    fi
    owner_ok=1
    kill -0 "$DATUM_PID" 2>/dev/null || owner_ok=0
    listener_exists "$PORT" || owner_ok=0
    listener_exists "$STRATUM_PORT" || owner_ok=0
    listener_exists "$API_PORT" || owner_ok=0
    if (( owner_ok == 1 )); then
        missing_owner_since=0
    elif (( missing_owner_since == 0 )); then
        missing_owner_since=$(date +%s)
    elif (( $(date +%s) - missing_owner_since >= 15 )); then
        echo "ABORT: bridge or DATUM owner watchdog failed for 15 seconds"
        exit 2
    fi
    sleep 2
done
