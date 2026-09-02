#!/usr/bin/env bash
# Container entrypoint for one Edge Grid node.
#
# It does three things before handing over to `python -m discovery.node`, and
# each of them emits a JSON line on stdout so the launcher records it rather
# than having to infer it. The node's own stdout is a stream of one JSON object
# per line; these events join that stream and use the same "event" key, so
# discovery/run_swarm.py parses them with the same reader.
#
#   1. Verifies EG_ADVERTISE_IP is genuinely configured on an interface in this
#      network namespace. A node advertises this address to every peer, and if
#      compose assigned something else the mesh fails with no useful error - the
#      peers simply dial an address that is not us. Better to refuse to start.
#   2. Optionally attaches `tc netem` to the container's interface. This needs
#      NET_ADMIN. If the capability is absent the failure is RECORDED and the
#      node starts anyway with an unshaped link, because a run that quietly
#      pretends to have 50 ms of latency is worse than one that says it does not.
#   3. Probes the Ollama endpoint the node was pointed at. `discovery.node`
#      itself never calls Ollama - the auction is pure protocol - but the whole
#      point of host.docker.internal here is that a container CAN reach the
#      host's inference server, so whether it can is worth one line of evidence.
#
# Everything after the flags below is passed straight through to discovery.node.

set -euo pipefail

emit() { printf '%s\n' "$1"; }

json_escape() { printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'; }

NAME="${EG_NAME:-unnamed}"

if [ -z "${EG_ADVERTISE_IP:-}" ]; then
    emit "{\"event\": \"entrypoint_error\", \"node\": \"$NAME\", \"error\": \"EG_ADVERTISE_IP is not set\"}"
    exit 64
fi

# --- 1. the advertised address must be real ---------------------------------
IFACE_IPS="$(ip -4 -o addr show | awk '{print $2" "$4}')"
if ! printf '%s\n' "$IFACE_IPS" | awk '{split($2,a,"/"); print a[1]}' | grep -qx "$EG_ADVERTISE_IP"; then
    emit "{\"event\": \"entrypoint_error\", \"node\": \"$NAME\", \"error\": \"EG_ADVERTISE_IP $EG_ADVERTISE_IP is on no interface\", \"interfaces\": $(json_escape "$IFACE_IPS")}"
    exit 65
fi

NETEM_DEV="${EG_NETEM_DEV:-eth0}"
emit "{\"event\": \"container\", \"node\": \"$NAME\", \"advertise_ip\": \"$EG_ADVERTISE_IP\", \"hostname\": \"$(hostname)\", \"interfaces\": $(json_escape "$IFACE_IPS"), \"netem_dev\": \"$NETEM_DEV\"}"

# --- 2. optional latency injection ------------------------------------------
NETEM_MS="${EG_NETEM_MS:-0}"
# Compared numerically, not as a string: the launcher renders floats, so "0.0"
# has to mean the same as "0" or every unshaped run would try to attach a
# 0.0 ms qdisc and report a failure that is purely an artefact of formatting.
if ! awk -v x="$NETEM_MS" 'BEGIN { exit !(x + 0 > 0) }'; then
    emit "{\"event\": \"netem\", \"node\": \"$NAME\", \"applied\": false, \"delay_ms\": 0, \"reason\": \"not requested\"}"
else
    # Written as ifs, not `[ ... ] && ...`, because under `set -e` a false test
    # at the end of a pipeline terminates the script.
    NETEM_ARGS=(delay "${NETEM_MS}ms")
    if [ -n "${EG_NETEM_JITTER_MS:-}" ]; then NETEM_ARGS+=("${EG_NETEM_JITTER_MS}ms"); fi
    if [ -n "${EG_NETEM_LOSS_PCT:-}" ]; then NETEM_ARGS+=(loss "${EG_NETEM_LOSS_PCT}%"); fi
    if TC_ERR="$(tc qdisc add dev "$NETEM_DEV" root netem "${NETEM_ARGS[@]}" 2>&1)"; then
        # Read the qdisc back rather than trusting the exit status: this is the
        # only proof the shaping is actually attached to the interface.
        QDISC="$(tc qdisc show dev "$NETEM_DEV" | head -1)"
        emit "{\"event\": \"netem\", \"node\": \"$NAME\", \"applied\": true, \"delay_ms\": $NETEM_MS, \"dev\": \"$NETEM_DEV\", \"qdisc\": $(json_escape "$QDISC")}"
    else
        emit "{\"event\": \"netem\", \"node\": \"$NAME\", \"applied\": false, \"delay_ms\": $NETEM_MS, \"dev\": \"$NETEM_DEV\", \"reason\": \"tc failed (NET_ADMIN missing?)\", \"error\": $(json_escape "$TC_ERR")}"
    fi
fi

# --- 3. is the host's inference server reachable from in here? ---------------
if [ "${EG_PROBE_OLLAMA:-1}" = "1" ]; then
    python3 - "$NAME" <<'PY'
import json, os, sys, time, urllib.error, urllib.request

name = sys.argv[1]
host = os.environ.get("OLLAMA_HOST", "")
row = {"event": "ollama_probe", "node": name, "host": host, "reachable": False}
if not host:
    row["error"] = "OLLAMA_HOST is not set"
else:
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=3) as r:
            body = json.loads(r.read().decode())
        row["reachable"] = True
        row["status"] = r.status
        row["models"] = sorted(m.get("name", "") for m in body.get("models", []))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["probe_ms"] = round((time.monotonic() - t0) * 1000, 1)
print(json.dumps(row), flush=True)
PY
fi

exec python3 -m discovery.node "$@"
