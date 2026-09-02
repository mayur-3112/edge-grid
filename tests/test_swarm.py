"""Tests for the containerised multi-host deployment.

Most of what can go wrong with a swarm is decided before a container ever
starts: whether every node really gets a distinct address, whether the star
topology survives being rendered into compose, whether the committed example and
the launcher still agree, and whether the entrypoint reports a latency injection
it did not manage to apply. All of that is unit-testable with no Docker at all,
and it is tested here.

Only the last two tests need a live daemon and the built image, and they carry
the `live` marker. `make test` skips them; `make test-live` runs them.

The entrypoint tests run the real deploy/grid/entrypoint.sh under bash with a
stub PATH - a `tc` that refuses, an `ip` that reports a fixed address, a
`python3` that intercepts only the final `-m discovery.node` exec. That is worth
the setup: the netem-skip path is the one place in this track where a degraded
run could quietly look like a clean one, so it is exercised rather than trusted.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from discovery import run_swarm as S
from edgegrid import config as C

REPO = C.REPO_ROOT
DEPLOY = REPO / "deploy" / "grid"
ENTRYPOINT = DEPLOY / "entrypoint.sh"


def services(spec: dict) -> dict:
    return spec["services"]


def command_of(spec: dict, node: str) -> list[str]:
    return spec["services"][node]["command"]


def flag(cmd: list[str], name: str) -> str:
    """The single value of `--name` in a command list."""
    return cmd[cmd.index(name) + 1]


def flags(cmd: list[str], name: str) -> list[str]:
    return [cmd[i + 1] for i, tok in enumerate(cmd) if tok == name]


# --------------------------------------------------------------------------
# addressing
# --------------------------------------------------------------------------

def test_node_ips_are_distinct_sequential_and_clear_of_the_gateway():
    ips = S.node_ips(5, "10.77.0.0/24", 10)
    assert ips == ["10.77.0.10", "10.77.0.11", "10.77.0.12",
                   "10.77.0.13", "10.77.0.14"]
    assert len(set(ips)) == 5
    assert "10.77.0.1" not in ips  # docker takes .1 for the bridge itself


def test_node_ips_rejects_an_offset_that_would_collide_with_the_gateway():
    with pytest.raises(ValueError, match="gateway"):
        S.node_ips(3, "10.77.0.0/24", 1)


def test_node_ips_rejects_a_subnet_too_small_for_the_swarm():
    with pytest.raises(ValueError, match="cannot hold"):
        S.node_ips(200, "10.77.0.0/28", 10)


# --------------------------------------------------------------------------
# topology
# --------------------------------------------------------------------------

def test_every_node_gets_its_own_address_and_the_same_port():
    """The single clearest difference from run_network: N peers, N addresses,
    one port. On shared loopback the port must differ per node; here it must
    not, because each node owns its own network namespace."""
    peers = [f"peer{i}" for i in range(4)]
    spec = S.compose_spec(4, peer_ids=peers, port=4001)
    addrs = [s["networks"]["gridnet"]["ipv4_address"] for s in services(spec).values()]
    assert len(set(addrs)) == 4
    ports = {flag(s["command"], "--port") for s in services(spec).values()}
    assert ports == {"4001"}


def test_each_node_binds_all_interfaces_and_advertises_its_own_address():
    """A node bound to 0.0.0.0 that also advertises 0.0.0.0 is undialable, and
    discovery.node refuses to start that way. The compose must therefore pair
    a wildcard bind with the container's real address."""
    peers = [f"peer{i}" for i in range(3)]
    spec = S.compose_spec(3, peer_ids=peers)
    for name, svc in services(spec).items():
        cmd = svc["command"]
        assert flag(cmd, "--listen-ip") == "0.0.0.0"
        advertised = flag(cmd, "--advertise-ip")
        assert advertised != "0.0.0.0"
        assert advertised == svc["networks"]["gridnet"]["ipv4_address"]
        assert svc["environment"]["EG_ADVERTISE_IP"] == advertised


def test_star_topology_matches_run_network():
    """Node 0 is requester and bootstrap; providers dial it and nobody else.

    That is what keeps a provider's DHT lookup of another provider genuinely
    remote instead of a local cache read, so it has to survive the translation
    into compose."""
    peers = [f"peer{i}" for i in range(4)]
    spec = S.compose_spec(4, peer_ids=peers, port=4001, subnet="10.77.0.0/24",
                          ip_offset=10)
    req = command_of(spec, "node0")
    assert flag(req, "--role") == "requester"
    assert flag(req, "--wait-peers") == "3"
    assert "--bootstrap" not in req

    for i in (1, 2, 3):
        cmd = command_of(spec, f"node{i}")
        assert flag(cmd, "--role") == "provider"
        assert flags(cmd, "--bootstrap") == ["/ip4/10.77.0.10/tcp/4001/p2p/peer0"]
        assert flag(cmd, "--wait-peers") == "1"


def test_every_node_probes_every_other_peer_over_the_dht():
    peers = [f"peer{i}" for i in range(3)]
    spec = S.compose_spec(3, peer_ids=peers)
    for i in range(3):
        probed = flags(command_of(spec, f"node{i}"), "--dht-probe")
        assert sorted(probed) == sorted(p for p in peers if p != f"peer{i}")


def test_price_and_ttft_ladder_matches_the_process_launcher():
    """The two harnesses must differ in transport and nothing else, or the
    comparison is between two different auctions."""
    spec = S.compose_spec(4, peer_ids=[f"peer{i}" for i in range(4)],
                          base_price=0.05, price_step=0.02,
                          base_ttft_ms=1200.0, ttft_step_ms=100.0)
    assert [flag(command_of(spec, f"node{i}"), "--price") for i in (1, 2, 3)] \
        == ["0.07", "0.09", "0.11"]
    assert [flag(command_of(spec, f"node{i}"), "--ttft-ms") for i in (1, 2, 3)] \
        == ["1300.0", "1400.0", "1500.0"]


def test_warm_and_forge_flags_land_on_the_named_providers_only():
    spec = S.compose_spec(4, peer_ids=[f"peer{i}" for i in range(4)],
                          warm_nodes=(1,), forge_nodes=(3,))
    assert "--warm" in command_of(spec, "node1")
    assert "--warm" not in command_of(spec, "node2")
    assert "--forge-bids" in command_of(spec, "node3")
    assert "--forge-bids" not in command_of(spec, "node1")


def test_a_swarm_needs_at_least_a_requester_and_a_provider():
    with pytest.raises(ValueError, match="at least"):
        S.compose_spec(1, peer_ids=["peer0"])


def test_peer_id_count_must_match_node_count():
    with pytest.raises(ValueError, match="peer ids"):
        S.compose_spec(3, peer_ids=["a", "b"])


# --------------------------------------------------------------------------
# the example rendering, which cannot carry a real key
# --------------------------------------------------------------------------

def test_the_example_takes_its_bootstrap_from_the_environment():
    """No PeerID exists without a private key, and no key is committed, so the
    example must ask for the address rather than invent one."""
    spec = S.compose_spec(3, peer_ids=None)
    for i in (1, 2):
        boot = flags(command_of(spec, f"node{i}"), "--bootstrap")
        assert boot == [S.BOOTSTRAP_PLACEHOLDER]
        assert boot[0].startswith("${EG_BOOTSTRAP")
    # DHT probes are addressed by PeerID, so they cannot appear either.
    assert flags(command_of(spec, "node0"), "--dht-probe") == []


# --------------------------------------------------------------------------
# capabilities, mounts, host access
# --------------------------------------------------------------------------

def test_net_admin_is_granted_only_when_shaping_is_requested():
    plain = S.compose_spec(3, peer_ids=[f"p{i}" for i in range(3)], latency_ms=0)
    assert all("cap_add" not in s for s in services(plain).values())

    shaped = S.compose_spec(3, peer_ids=[f"p{i}" for i in range(3)], latency_ms=25)
    assert all(s["cap_add"] == ["NET_ADMIN"] for s in services(shaped).values())
    assert all(s["environment"]["EG_NETEM_MS"] == "25" for s in services(shaped).values())

    withheld = S.compose_spec(3, peer_ids=[f"p{i}" for i in range(3)],
                              latency_ms=25, cap_net_admin=False)
    assert all("cap_add" not in s for s in services(withheld).values())
    # The request still reaches the container, so the entrypoint reports the
    # skip rather than the launcher silently not asking.
    assert all(s["environment"]["EG_NETEM_MS"] == "25"
               for s in services(withheld).values())


def test_identities_are_mounted_read_only():
    """A container that could write the key directory could mint a wallet the
    launcher never recorded."""
    spec = S.compose_spec(3, peer_ids=[f"p{i}" for i in range(3)], key_dir="/k")
    for svc in services(spec).values():
        assert svc["volumes"] == ["/k:/keys:ro"]
        assert flag(svc["command"], "--key-dir") == "/keys"


def test_ollama_is_reached_on_the_host_not_in_the_swarm():
    spec = S.compose_spec(3, peer_ids=[f"p{i}" for i in range(3)])
    for svc in services(spec).values():
        assert "host.docker.internal:host-gateway" in svc["extra_hosts"]
        assert svc["environment"]["OLLAMA_HOST"] == "http://host.docker.internal:11434"


def test_the_network_is_a_user_defined_bridge_with_a_fixed_subnet():
    spec = S.compose_spec(3, peer_ids=[f"p{i}" for i in range(3)],
                          subnet="10.77.0.0/24", project="proj")
    net = spec["networks"]["gridnet"]
    assert net["driver"] == "bridge"
    assert net["name"] == "proj-net"
    assert net["ipam"]["config"] == [{"subnet": "10.77.0.0/24"}]


# --------------------------------------------------------------------------
# YAML emitter
# --------------------------------------------------------------------------

def test_rendered_compose_parses_back_to_the_spec_it_came_from():
    """run_swarm emits YAML by hand rather than depending on a package that is
    not in requirements.txt, so the emitter is checked against a real parser."""
    yaml = pytest.importorskip("yaml")
    spec = S.compose_spec(4, peer_ids=[f"peer{i}" for i in range(4)],
                          latency_ms=25, key_dir="/tmp/k")
    assert yaml.safe_load(S.to_yaml(spec)) == spec


def test_the_emitter_quotes_values_yaml_would_otherwise_mangle():
    yaml = pytest.importorskip("yaml")
    tricky = {"a": "0.0.0.0", "b": "3s", "c": "--role", "d": "yes", "e": "null",
              "f": S.BOOTSTRAP_PLACEHOLDER, "g": True, "h": 4001, "i": []}
    assert yaml.safe_load(S.to_yaml(tricky)) == tricky


def test_the_emitter_refuses_a_shape_it_cannot_render():
    """Better a loud failure than a compose file that parses into the wrong
    document."""
    with pytest.raises(ValueError, match="not supported"):
        S.to_yaml({"x": [{"nested": {"too": "deep"}}]})


# --------------------------------------------------------------------------
# drift between the committed artefacts and the code
# --------------------------------------------------------------------------

def test_committed_compose_matches_generator():
    """deploy/grid/compose.yml is generated. If this fails, run:
        python -m discovery.run_swarm --write-compose"""
    assert S.EXAMPLE_COMPOSE.exists()
    assert S.EXAMPLE_COMPOSE.read_text() == S.render_compose(3, key_dir="./keys")


def test_node_requirements_match_repo_requirements():
    """The image must not silently install a different pin than the host."""
    repo = {ln.strip() for ln in (REPO / "requirements.txt").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}
    node = [ln.strip() for ln in (DEPLOY / "requirements-node.txt").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]
    assert node, "requirements-node.txt is empty"
    missing = [ln for ln in node if ln not in repo]
    assert not missing, f"pins not present verbatim in requirements.txt: {missing}"


def test_dockerfile_installs_what_py_libp2p_actually_needs():
    text = (DEPLOY / "Dockerfile").read_text()
    assert "libgmp-dev" in text      # fastecdsa 2.3.2 needs gmp.h at build time
    assert "libgmp10" in text        # and the shared library at run time
    assert "iproute2" in text        # tc, for the netem path
    assert "COPY edgegrid" in text and "COPY discovery" in text


def test_the_image_carries_no_host_venv_and_no_secrets():
    text = (DEPLOY / "Dockerfile").read_text()
    ignore = (DEPLOY / "Dockerfile.dockerignore").read_text()
    assert ".venv" not in text.replace("# ", "")  # never copied in
    assert ".venv/" in ignore                     # and never even sent as context
    assert ".env" in ignore
    for secretish in ("PRIVATE_KEY", "GROQ_API_KEY", "--key ", "BEGIN "):
        assert secretish not in text


# --------------------------------------------------------------------------
# the entrypoint, run for real under bash with a stub PATH
# --------------------------------------------------------------------------

def _stub_bin(tmp_path: Path, *, tc_rc: int, ip_addr: str = "10.77.0.10") -> Path:
    """A PATH containing a fake `tc` and `ip`, and a `python3` that intercepts
    only the final `-m discovery.node` exec and passes everything else through
    to the real interpreter (the entrypoint uses python for JSON escaping)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    (bin_dir / "ip").write_text(
        "#!/bin/sh\n"
        "echo '1: lo    inet 127.0.0.1/8 scope host lo'\n"
        f"echo '2: eth0    inet {ip_addr}/24 brd 10.77.0.255 scope global eth0'\n")
    complaint = ("echo 'RTNETLINK answers: Operation not permitted' >&2\n"
                 if tc_rc else "")
    (bin_dir / "tc").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *show*) echo "qdisc netem 8001: root refcnt 2 limit 1000 delay 25ms"; exit 0 ;;\n'
        "esac\n"
        + complaint
        + f"exit {tc_rc}\n")
    (bin_dir / "python3").write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "discovery.node" ]; then\n'
        '    printf \'{"event": "stub_node"}\\n\'; exit 0\n'
        "  fi\n"
        "done\n"
        f'exec {sys.executable} "$@"\n')
    for f in bin_dir.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run_entrypoint(tmp_path: Path, env_extra: dict, *, tc_rc: int = 1,
                    ip_addr: str = "10.77.0.10") -> tuple[int, list[dict], str]:
    bin_dir = _stub_bin(tmp_path, tc_rc=tc_rc, ip_addr=ip_addr)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["EG_PROBE_OLLAMA"] = "0"
    env.update(env_extra)
    out = subprocess.run(["bash", str(ENTRYPOINT), "--name", "x"],
                         capture_output=True, text=True, env=env, timeout=60)
    events = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return out.returncode, events, out.stderr


bash_required = pytest.mark.skipif(shutil.which("bash") is None,
                                   reason="needs bash")


@bash_required
def test_entrypoint_records_a_latency_injection_it_could_not_apply(tmp_path):
    """The whole point: a run without NET_ADMIN carries on unshaped and SAYS SO.
    A row claiming 25 ms it never had would be worse than no row at all."""
    rc, events, _ = _run_entrypoint(
        tmp_path, {"EG_NAME": "node0", "EG_ADVERTISE_IP": "10.77.0.10",
                   "EG_NETEM_MS": "25"}, tc_rc=1)
    assert rc == 0
    netem = next(e for e in events if e["event"] == "netem")
    assert netem["applied"] is False
    assert netem["delay_ms"] == 25
    assert "NET_ADMIN" in netem["reason"]
    assert "Operation not permitted" in netem["error"]
    # and the node was still started
    assert any(e["event"] == "stub_node" for e in events)


@bash_required
def test_entrypoint_reads_the_qdisc_back_before_claiming_it_applied(tmp_path):
    rc, events, _ = _run_entrypoint(
        tmp_path, {"EG_NAME": "node0", "EG_ADVERTISE_IP": "10.77.0.10",
                   "EG_NETEM_MS": "25"}, tc_rc=0)
    assert rc == 0
    netem = next(e for e in events if e["event"] == "netem")
    assert netem["applied"] is True
    assert "delay 25ms" in netem["qdisc"]


@bash_required
def test_entrypoint_treats_a_float_zero_as_no_shaping_requested(tmp_path):
    """The launcher renders floats, so "0.0" must not be read as a request for
    a 0.0 ms qdisc and then reported as a failure."""
    rc, events, _ = _run_entrypoint(
        tmp_path, {"EG_NAME": "node0", "EG_ADVERTISE_IP": "10.77.0.10",
                   "EG_NETEM_MS": "0.0"}, tc_rc=1)
    assert rc == 0
    netem = next(e for e in events if e["event"] == "netem")
    assert netem["applied"] is False
    assert netem["reason"] == "not requested"
    assert "error" not in netem


@bash_required
def test_entrypoint_refuses_to_advertise_an_address_it_does_not_hold(tmp_path):
    """Advertising an address that is on no interface hands every peer a dial
    target that is not us, and the mesh then fails for no visible reason."""
    rc, events, _ = _run_entrypoint(
        tmp_path, {"EG_NAME": "node0", "EG_ADVERTISE_IP": "10.77.0.99"},
        ip_addr="10.77.0.10")
    assert rc == 65
    err = next(e for e in events if e["event"] == "entrypoint_error")
    assert "10.77.0.99" in err["error"]
    assert not any(e["event"] == "stub_node" for e in events)


@bash_required
def test_entrypoint_refuses_to_start_without_an_advertised_address(tmp_path):
    env = {"EG_NAME": "node0"}
    rc, events, _ = _run_entrypoint(tmp_path, env)
    assert rc == 64
    assert events[0]["event"] == "entrypoint_error"


# --------------------------------------------------------------------------
# comparison table
# --------------------------------------------------------------------------

def _auction(n, topology_ms, ok="True"):
    return {"n_nodes": str(n), "ok": ok, "first_bid_ms": str(topology_ms),
            "last_bid_ms": str(topology_ms + 5),
            "broadcast_to_award_ms": "2005", "mesh_ready_ms": "9000"}


def test_comparison_pairs_the_two_topologies_and_reports_the_delta():
    cont = [_auction(3, 20), _auction(3, 30)]
    proc = [_auction(3, 10), _auction(3, 20)]
    rows = S.compare(cont, proc)
    assert len(rows) == 1
    row = rows[0]
    assert row["n_container_auctions"] == 2 and row["n_process_auctions"] == 2
    assert row["first_bid_ms_container_mean"] == 25.0
    assert row["first_bid_ms_process_mean"] == 15.0
    assert row["first_bid_ms_delta_mean"] == 10.0


def test_comparison_excludes_failed_auctions_and_says_how_many_remained():
    cont = [_auction(3, 20), _auction(3, 900, ok="False")]
    proc = [_auction(3, 10)]
    row = S.compare(cont, proc)[0]
    assert row["n_container_auctions"] == 1
    assert row["first_bid_ms_container_mean"] == 20.0
    # one sample each, so no standard deviation is claimed
    assert row["first_bid_ms_container_sd"] == ""


def test_comparison_leaves_a_missing_side_blank_rather_than_zero():
    row = S.compare([_auction(5, 20)], [])[0]
    assert row["n_process_auctions"] == 0
    assert row["first_bid_ms_process_mean"] == ""
    assert row["first_bid_ms_delta_mean"] == ""


# --------------------------------------------------------------------------
# live: a real swarm
# --------------------------------------------------------------------------

def _image_present(image: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", image],
                          capture_output=True).returncode == 0


@pytest.mark.live
@pytest.mark.slow
def test_live_three_container_swarm_forms_a_mesh_and_clears_an_auction(
        tmp_path, monkeypatch):
    ok, detail = S.docker_ok()
    if not ok:
        pytest.skip(f"docker unusable: {detail}")
    if not _image_present(S.DEFAULT_IMAGE):
        pytest.skip(f"{S.DEFAULT_IMAGE} not built; run "
                    f"`python -m discovery.run_swarm --build --nodes 3`")

    monkeypatch.setattr(C, "RESULTS_DIR", tmp_path / "results")
    rc = S.main(["--nodes", "3", "--repeats", "1",
                 "--key-dir", str(tmp_path / "keys"),
                 "--project", "edgegrid-swarm-pytest",
                 "--experiment", "pytest-swarm"])
    assert rc == 0

    run_dir = sorted((tmp_path / "results").glob("pytest-swarm-*"))[-1]
    net = json.loads((run_dir / "network-n3-r0.json").read_text())
    addrs = [v.split("/")[0] for v in net["containers"].values()]
    assert len(set(addrs)) == 3, f"containers share an address: {net}"
    assert net["gateway"] not in addrs

    import csv
    rows = list(csv.DictReader((run_dir / "auctions.csv").open()))
    assert len(rows) == 1
    row = rows[0]
    assert row["ok"] == "True", row["error"]
    assert row["distinct_ips"] == "3"
    assert int(row["n_eligible"]) == 2
    assert row["winner_ack"] == "True"
    assert float(row["clearing_price"]) > 0

    nodes = list(csv.DictReader((run_dir / "nodes.csv").open()))
    assert len({n["advertise_ip"] for n in nodes}) == 3
    assert len({n["peer_id"] for n in nodes}) == 3


@pytest.mark.live
@pytest.mark.slow
def test_live_netem_actually_delays_the_bids(tmp_path, monkeypatch):
    """Proof the shaping is real and not just a recorded intention: with 25 ms
    each way the first bid cannot arrive as fast as it does unshaped."""
    ok, _ = S.docker_ok()
    if not ok or not _image_present(S.DEFAULT_IMAGE):
        pytest.skip("needs docker and the built node image")

    monkeypatch.setattr(C, "RESULTS_DIR", tmp_path / "results")
    rc = S.main(["--nodes", "3", "--repeats", "1", "--latency-ms", "25",
                 "--key-dir", str(tmp_path / "keys"),
                 "--project", "edgegrid-swarm-pytest-netem",
                 "--experiment", "pytest-swarm-netem"])
    assert rc == 0

    import csv
    run_dir = sorted((tmp_path / "results").glob("pytest-swarm-netem-*"))[-1]
    row = list(csv.DictReader((run_dir / "auctions.csv").open()))[0]
    assert row["ok"] == "True", row["error"]
    if int(row["netem_applied_nodes"]) != 3:
        pytest.skip(f"netem not permitted here: {row['netem_skip_reasons']}")
    assert "delay 25ms" in row["netem_qdiscs"]
    # one hop out for the job, one back for the bid
    assert float(row["first_bid_ms"]) >= 50.0, row
