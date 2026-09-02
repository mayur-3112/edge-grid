/* edge grid — operator console
 *
 * Vanilla JS, no build step, no CDN. The page must render something truthful
 * with the backend down, so every fetch is guarded and every panel has an empty
 * state that says what is missing rather than showing a blank box.
 *
 * Live state comes from /api/events (SSE). A slow reconcile poll of /api/jobs
 * heals any gap left by a dropped connection — the event bus tells a client when
 * it fell behind, and this is how the client catches up.
 */
"use strict";

const S = {
  mode: null, modeReason: "", health: null,
  nodes: [], jobs: new Map(), order: [],
  stats: {}, settlements: [], stakes: [], ledgerTotals: null, config: {},
  events: [], connected: false, seq: 0, streaming: false,
};

const STAGES = ["auction", "inference", "commit", "verify", "settle"];
const STAGE_ABBR = { auction: "auc", inference: "inf", commit: "com", verify: "ver", settle: "set" };

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
};
const fmt = (v, d = 1) => (v === null || v === undefined || v === "" ? "—" : Number(v).toFixed(d));
const int = (v) => (v === null || v === undefined || v === "" ? "—" : String(v));
const shortId = (p) => (p ? String(p).slice(-12) : "—");
const clock = (ms) => new Date(ms || Date.now()).toTimeString().slice(0, 8);
const trunc = (s, n) => { s = String(s || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1) + "…" : s; };

/* ---------------------------------------------------------------- fetching */

async function getJSON(path) {
  const r = await fetch(path, { headers: { accept: "application/json" } });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

function offline(why) {
  S.connected = false;
  const b = $("banner");
  b.className = "banner show";
  b.textContent = "Gateway unreachable — " + why + ". panels below show the last known state. retrying.";
  setChip("chip-feed", "feed offline", false);
}

function online() {
  S.connected = true;
  $("banner").className = "banner";
}

function setChip(id, text, good) {
  const c = $(id);
  c.className = "chip" + (good === true ? " good" : good === false ? " bad" : "");
  c.lastElementChild.textContent = text;
}

/* ------------------------------------------------------------------ boot */

async function boot() {
  await refreshHealth();
  await Promise.all([refreshConfig(), refreshModels(), reconcileJobs(), refreshStats(),
                     refreshSettlements()]);
  connectEvents();
  setInterval(refreshStats, 4000);
  setInterval(refreshSettlements, 6000);
  setInterval(reconcileJobs, 8000);
  setInterval(refreshHealth, 15000);
}

async function refreshHealth() {
  try {
    const r = await fetch("/health");
    const h = await r.json();
    S.health = h;
    S.mode = h.mode;
    S.modeReason = h.mode_reason || "";
    online();
    setChip("chip-mode", "mode " + h.mode, h.mode === "p2p" ? true : null);
    const rt = h.inference_runtime || {};
    setChip("chip-runtime", rt.reachable ? "runtime ollama" : "runtime down", !!rt.reachable);
    setChip("chip-judge", "judge " + (h.judge ? h.judge.backend : "?"),
            h.judge && h.judge.backend !== "mock" ? null : false);
    if (!rt.reachable) {
      const b = $("banner");
      b.className = "banner show";
      b.textContent = "Inference runtime unreachable at " + (rt.endpoint || "?") + " — " + (rt.error || "") +
                      ". jobs will fail at the inference stage rather than return a fabricated answer.";
    }
    renderFootnote();
  } catch (e) { offline(String(e.message || e)); }
}

async function refreshConfig() {
  // The dashboard states the warm-start discount as a number; read it from the
  // gateway's own config snapshot rather than hard-coding a second copy of it.
  try { S.config = await getJSON("/api/config"); } catch (e) { S.config = {}; }
}

async function refreshModels() {
  const sel = $("model");
  try {
    const d = await getJSON("/v1/models");
    sel.innerHTML = "";
    (d.data || []).forEach((m) => {
      const o = el("option", null, m.id + (m.edgegrid_warm_providers ? "  ● warm on " + m.edgegrid_warm_providers : ""));
      o.value = m.id;
      sel.appendChild(o);
    });
    if (!sel.options.length) sel.appendChild(el("option", null, "(no models served)"));
  } catch (e) {
    sel.innerHTML = "";
    sel.appendChild(el("option", null, "(model list unavailable)"));
  }
}

async function refreshStats() {
  try {
    S.stats = await getJSON("/api/stats");
    online();
    renderStats();
  } catch (e) { offline(String(e.message || e)); renderStats(); }
}

async function refreshSettlements() {
  try {
    const d = await getJSON("/api/settlements");
    S.settlements = d.settlements || [];
    S.stakes = d.stakes || [];
    S.ledgerTotals = d.totals || null;
    renderSettlements();
  } catch (e) { renderSettlements(); }
}

async function reconcileJobs() {
  try {
    const d = await getJSON("/api/jobs?limit=60");
    (d.jobs || []).forEach((rec) => S.jobs.set(rec.job_id, rec));
    S.order = (d.jobs || []).map((j) => j.job_id);
    renderJobs();
    renderVerdicts();
    if (selected && S.jobs.has(selected)) renderAuction();
  } catch (e) { renderJobs(); }
}

/* ------------------------------------------------------------------ SSE */

function connectEvents() {
  let src;
  try { src = new EventSource("/api/events?replay=80"); }
  catch (e) { offline("EventSource failed: " + e); return; }

  src.addEventListener("hello", (m) => {
    const d = JSON.parse(m.data);
    S.mode = d.mode; S.modeReason = d.mode_reason;
    S.nodes = d.nodes || []; S.stats = d.stats || {};
    online();
    setChip("chip-feed", "feed live", true);
    $("chip-feed").firstElementChild.classList.add("live");
    renderNodes(); renderStats(); renderFootnote();
  });

  src.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch (e) { return; }
    S.seq = ev.seq;
    pushLog(ev);
    handle(ev);
  };

  src.onerror = () => {
    setChip("chip-feed", "feed reconnecting", false);
    $("chip-feed").firstElementChild.classList.remove("live");
    offline("event stream dropped");
  };
}

function ensureJob(id) {
  if (!S.jobs.has(id)) {
    S.jobs.set(id, { job_id: id, stages: {}, bids: [], notes: [], status: "running" });
    S.order.unshift(id);
  }
  return S.jobs.get(id);
}

function handle(ev) {
  switch (ev.type) {
    case "node":
      S.nodes = ev.nodes || S.nodes;
      renderNodes();
      break;
    case "job.created": {
      const j = ensureJob(ev.job_id);
      j.request = ev.job; j.question = ev.question; j.model = (ev.job || {}).model;
      j.created_ms = ev.ts_ms; j.mode = ev.mode; j.status = "running";
      STAGES.forEach((s) => (j.stages[s] = { state: "pending", ms: null }));
      renderJobs();
      break;
    }
    case "job.stage": {
      const j = ensureJob(ev.job_id);
      j.stages[ev.stage] = { state: ev.state, ms: ev.ms };
      if (ev.state === "error") j.status = "error";
      if (ev.stage === "settle" && ev.state === "ok") j.status = "complete";
      renderJobs();
      break;
    }
    case "bid": {
      const j = ensureJob(ev.job_id);
      j.bids = (j.bids || []).filter((b) => b.bidder_peer_id !== ev.bid.bidder_peer_id);
      j.bids.push(Object.assign({}, ev.bid, { effective_price: ev.effective_price }));
      if (selected === ev.job_id) renderAuction();
      break;
    }
    case "award": {
      const j = ensureJob(ev.job_id);
      j.award = ev.award;
      (j.bids || []).forEach((b) => (b.winner = b.bidder_peer_id === ev.award.winner_peer_id));
      if (!selected) selected = ev.job_id;
      renderJobs(); renderAuction();
      break;
    }
    case "inference.done": {
      const j = ensureJob(ev.job_id);
      j.result = ev.result;
      renderJobs();
      break;
    }
    case "commit": {
      const j = ensureJob(ev.job_id);
      j.commitment = ev.commitment; j.da = ev.da;
      if (selected === ev.job_id) renderAuction();
      break;
    }
    case "verdict": {
      const j = ensureJob(ev.job_id);
      j.verdict = ev.verdict; j.sampled = true;
      renderJobs(); renderVerdicts();
      break;
    }
    case "settlement": {
      const j = ensureJob(ev.job_id);
      j.settlement = ev.settlement;
      renderJobs();
      refreshSettlements();
      break;
    }
    case "log": {
      if (ev.job_id) {
        const j = ensureJob(ev.job_id);
        j.notes = j.notes || [];
        // The same note arrives twice: once live on the bus, once inside the job
        // record on the next reconcile poll. Dedupe on the text so the auction
        // panel does not show it twice.
        if (!j.notes.some((n) => n.message === ev.message)) {
          j.notes.push({ level: ev.level, message: ev.message });
          if (selected === ev.job_id) renderAuction();
        }
      }
      break;
    }
  }
}

/* -------------------------------------------------------------- rendering */

function renderStats() {
  const s = S.stats || {};
  $("s-jobs").textContent = int(s.jobs_total);
  $("s-jobs-sub").textContent = (s.jobs_complete || 0) + " complete · " + (s.jobs_error || 0) + " error";
  $("s-ttft").textContent = s.ttft_ms_mean === null || s.ttft_ms_mean === undefined ? "—" : Math.round(s.ttft_ms_mean);
  $("s-ttft-sub").textContent = s.ttft_ms_min ? "min " + Math.round(s.ttft_ms_min) + " · max " + Math.round(s.ttft_ms_max) : "ms, first token";
  $("s-tps").textContent = fmt(s.tokens_per_sec_mean, 1);
  $("s-grid").textContent = fmt(s.grid_escrowed, 4);
  $("s-grid-sub").textContent = "paid " + fmt(s.grid_paid, 4) + " · slashed " + fmt(s.grid_slashed, 4);
  $("s-ver").innerHTML = "";
  $("s-ver").appendChild(el("span", "ok", int(s.verdict_pass || 0)));
  $("s-ver").appendChild(el("span", "dim", " / "));
  $("s-ver").appendChild(el("span", "bad", int(s.verdict_fail || 0)));
  $("s-ver").appendChild(el("span", "dim", " / "));
  $("s-ver").appendChild(el("span", "ink2", int(s.verdict_error || 0)));
  $("s-ver-sub").textContent = "sampled " + int(s.jobs_sampled || 0) + " @ rate " + (s.sample_rate ?? "—");
  const da = s.da || {};
  $("s-da").textContent = int(da.height);
  $("s-da-sub").textContent = int(da.blobs) + " blobs · " + int(da.blocks) + " blocks";
  $("verify-rate").textContent = "sample rate " + (s.sample_rate ?? "—") + " · judge " + (s.judge_backend || "?");
  $("v-pass").textContent = int(s.verdict_pass || 0);
  $("v-fail").textContent = int(s.verdict_fail || 0);
  $("v-err").textContent = int(s.verdict_error || 0);
  $("events-count").textContent = "seq " + (S.seq || s.events || 0);
}

function renderNodes() {
  const body = $("nodes-body");
  body.innerHTML = "";
  $("nodes-empty").style.display = S.nodes.length ? "none" : "";
  $("nodes-count").textContent = S.nodes.length + " peers";
  S.nodes.forEach((n) => {
    const tr = el("tr");
    const peer = el("td");
    peer.appendChild(el("span", null, n.label + " "));
    peer.appendChild(el("span", "mono-id", shortId(n.peer_id)));
    if (n.executes) peer.appendChild(el("span", "dim", " ▮ runtime"));
    tr.appendChild(peer);
    tr.appendChild(el("td", "dim", "T" + n.tier + " " + n.tier_name.toLowerCase()));
    tr.appendChild(el("td", "r num", fmt(n.stake, 2)));
    tr.appendChild(el("td", "r num " + (n.earned > 0 ? "ok" : "dim"), fmt(n.earned, 4)));
    tr.appendChild(el("td", "dim", n.warm_models.length ? trunc(n.warm_models.join(" "), 22) : "—"));
    tr.appendChild(el("td", "r num", n.last_ttft_ms ? Math.round(n.last_ttft_ms) : "—"));
    tr.appendChild(el("td", "r num dim", fmt(n.price_per_1k, 2)));
    tr.appendChild(el("td", "r num", int(n.jobs_served)));
    const h = el("td");
    const pill = el("span", "pill " + (n.healthy ? "pass" : "fail"));
    pill.appendChild(el("span", "dot"));
    pill.appendChild(el("span", null, n.healthy ? "up" : "down"));
    h.appendChild(pill);
    tr.appendChild(h);
    body.appendChild(tr);
  });
}

let selected = null;

function renderJobs() {
  const body = $("jobs-body");
  body.innerHTML = "";
  const ids = S.order.slice(0, 40);
  $("jobs-empty").style.display = ids.length ? "none" : "";
  $("jobs-count").textContent = S.jobs.size + " jobs";
  ids.forEach((id) => {
    const j = S.jobs.get(id);
    if (!j) return;
    const tr = el("tr", "click" + (id === selected ? " sel" : ""));
    tr.onclick = () => { selected = id; renderJobs(); renderAuction(); };
    tr.appendChild(el("td", "mono-id", id.slice(0, 8)));

    const st = el("td");
    const track = el("div", "track");
    STAGES.forEach((s) => {
      const cell = (j.stages || {})[s] || { state: "pending" };
      track.appendChild(el("span", "stg " + cell.state, STAGE_ABBR[s]));
    });
    st.appendChild(track);
    tr.appendChild(st);

    tr.appendChild(el("td", "ink2", trunc(j.question || (j.request || {}).prompt, 30)));
    tr.appendChild(el("td", "r num", int((j.award || {}).n_bids)));
    tr.appendChild(el("td", "r num", (j.award ? fmt(j.award.clearing_price, 4) : "—")));
    tr.appendChild(el("td", "r num", (j.result ? Math.round(j.result.ttft_ms) : "—")));
    tr.appendChild(el("td", "r num", (j.result ? int(j.result.tokens_generated) : "—")));

    const v = el("td");
    if (j.verdict) {
      const k = j.verdict.verdict;
      const p = el("span", "pill " + (k === "pass" ? "pass" : k === "fail" ? "fail" : "err"), k);
      v.appendChild(p);
    } else if ((j.stages || {}).verify && j.stages.verify.state === "skipped") {
      v.appendChild(el("span", "dim", "unsampled"));
    } else { v.appendChild(el("span", "dim", "—")); }
    tr.appendChild(v);

    const e = el("td");
    if (j.settlement) {
      const state = j.settlement.state;
      const cls = state === "settled" ? "settled" : state === "slashed" ? "slashed" : "hold";
      e.appendChild(el("span", "pill " + cls, state === "awaiting_verification" ? "escrowed" : state));
    } else if (j.status === "error") {
      e.appendChild(el("span", "pill fail", "error"));
    } else { e.appendChild(el("span", "dim", "—")); }
    tr.appendChild(e);

    body.appendChild(tr);
  });
}

function renderAuction() {
  const host = $("auction-body");
  host.innerHTML = "";
  const j = selected ? S.jobs.get(selected) : null;
  if (!j || !(j.bids || []).length) {
    $("auction-job").textContent = j ? "job " + selected.slice(0, 8) + " — no bids" : "no job selected";
    host.appendChild(Object.assign(el("div", "empty"), {
      innerHTML: "<b>── no auction selected ──</b>pick a job in the feed to see its bid ladder and clearing price",
    }));
    return;
  }
  $("auction-job").textContent = "job " + selected.slice(0, 8) + " · " + j.bids.length + " bids";

  // Eligible bids first, cheapest effective price at the top; anything the auction
  // rejected is listed under them with its reason rather than dropped from view.
  const live = j.bids.filter((b) => b.eligible !== false)
                     .sort((a, b) => a.effective_price - b.effective_price);
  const out = j.bids.filter((b) => b.eligible === false);
  const bids = live.concat(out);
  const max = Math.max.apply(null, live.map((b) => b.effective_price).concat([0])) || 1;
  const winnerId = (j.award || {}).winner_peer_id;

  bids.forEach((b, i) => {
    const dead = b.eligible === false;
    const isWin = !dead && b.bidder_peer_id === winnerId;
    const isSecond = !dead && !isWin && i === 1;
    const row = el("div", "bid-row" + (isWin ? " win" : isSecond ? " second" : dead ? " out" : ""));
    row.appendChild(el("span", "dim num", dead ? "×" : "№" + (i + 1)));

    const mid = el("div");
    const label = el("div");
    label.appendChild(el("span", null, nodeLabel(b.bidder_peer_id) + " "));
    label.appendChild(el("span", "mono-id", shortId(b.bidder_peer_id)));
    if (b.warm) {
      const pct = Math.round((S.config.WARM_START_BONUS || 0) * 100);
      label.appendChild(el("span", "ok", "  ● warm −" + (pct || "?") + "%"));
    }
    mid.appendChild(label);
    const bar = el("div", "bar");
    const fill = el("i");
    fill.style.width = dead ? "0%" : Math.max(2, (b.effective_price / max) * 100) + "%";
    bar.appendChild(fill);
    if (dead) { bar.appendChild(el("span", "dim", "")); }
    mid.appendChild(bar);
    row.appendChild(mid);

    row.appendChild(el("span", "r num", fmt(b.price, 4)));
    row.appendChild(el("span", "r num dim", dead ? "—" : fmt(b.effective_price, 4)));
    row.appendChild(el("span", "r num dim", Math.round(b.estimated_ttft_ms) + "ms"));
    if (dead) row.title = "rejected: " + b.reason;
    host.appendChild(row);
  });

  if (j.award) {
    const c = el("div", "clearing");
    const add = (k, v, cls) => {
      const d = el("div");
      d.appendChild(el("span", "dim", k + " "));
      d.appendChild(el("b", cls, v));
      c.appendChild(d);
    };
    add("winner", nodeLabel(j.award.winner_peer_id) + " " + shortId(j.award.winner_peer_id), "ok");
    add("own bid", fmt(j.award.winning_bid_price, 4) + " GRID");
    add("→ clearing (2nd price)", fmt(j.award.clearing_price, 4) + " GRID", "bad");
    add("auction", fmt(j.award.auction_ms, 2) + " ms");
    if (j.auction) add("bids", j.auction.received + " received · " + j.auction.eligible +
                       " eligible" + (Object.keys(j.auction.rejected || {}).length
                         ? " · rejected " + JSON.stringify(j.auction.rejected) : ""));
    if (j.da) add("da root", String(j.da.root || "").slice(0, 16) + "… h" + j.da.height +
                  " proof " + j.da.proof_len);
    host.appendChild(c);
  }
  (j.notes || []).forEach((n) => {
    const d = el("div", "clearing");
    d.appendChild(el("span", n.level === "info" ? "dim" : "bad",
                     "▮ " + n.message));
    host.appendChild(d);
  });
}

function nodeLabel(peerId) {
  const n = S.nodes.find((x) => x.peer_id === peerId);
  return n ? n.label : "peer";
}

function renderVerdicts() {
  const body = $("verdicts-body");
  body.innerHTML = "";
  const rows = S.order.map((id) => S.jobs.get(id)).filter((j) => j && j.verdict).slice(0, 30);
  $("verdicts-empty").style.display = rows.length ? "none" : "";
  rows.forEach((j) => {
    const v = j.verdict;
    const tr = el("tr");
    tr.appendChild(el("td", "mono-id", j.job_id.slice(0, 8)));
    const k = el("td");
    k.appendChild(el("span", "pill " + (v.verdict === "pass" ? "pass" : v.verdict === "fail" ? "fail" : "err"),
                     v.verdict));
    tr.appendChild(k);
    tr.appendChild(el("td", "r num", v.quality_score === null || v.quality_score === undefined ? "—" : v.quality_score + "/5"));
    tr.appendChild(el("td", "dim", trunc(v.judge_backend + " " + (v.judge_model || ""), 22)));
    tr.appendChild(el("td", "r " + (v.blob_verified ? "ok" : "bad"), v.blob_verified ? "ok" : "bad"));
    tr.title = v.reason || "";
    body.appendChild(tr);
  });
}

function renderSettlements() {
  const stakes = $("stakes");
  stakes.innerHTML = "";
  const maxStake = Math.max.apply(null, S.stakes.map((s) => s.opening_stake || 1).concat([1]));
  S.stakes.forEach((s) => {
    const row = el("div", "stake-row");
    row.appendChild(el("span", null, s.label));
    const bar = el("div", "bar");
    const fill = el("i");
    const pct = Math.max(1, (s.stake / maxStake) * 100);
    fill.style.width = pct + "%";
    fill.style.background = s.stake < s.opening_stake ? "var(--pink)" : "var(--line-2)";
    bar.appendChild(fill);
    row.appendChild(bar);
    const right = el("span", "r num");
    right.appendChild(el("span", s.stake < s.opening_stake ? "bad" : "ink2", fmt(s.stake, 2)));
    if (s.earned) right.appendChild(el("span", "ok", " +" + fmt(s.earned, 4)));
    row.appendChild(right);
    stakes.appendChild(row);
  });

  const body = $("ledger-body");
  body.innerHTML = "";
  $("ledger-empty").style.display = S.settlements.length ? "none" : "";
  // The row count alone overstates the ledger once an audit has reversed a row.
  // Report the value that actually moved, from the server's own totals.
  const t = S.ledgerTotals;
  $("ledger-count").textContent = t
    ? t.rows + " records \u00b7 " + t.rows_live + " live \u00b7 " + fmt(t.paid, 4) + " GRID paid"
    : S.settlements.length + " records";
  S.settlements.slice(0, 40).forEach((r) => {
    const tr = el("tr", r.reversed ? "reversed" : null);
    const idc = el("td", "mono-id", r.job_id.slice(0, 8));
    // An operator re-audit produces a second record for the same job; mark it so
    // the two are never read as one job paid twice. The superseded row is struck
    // through - its value movement has been reversed and is no longer real money.
    if (r.audit) idc.appendChild(el("span", "bad", " \u2116audit"));
    if (r.reversed) idc.appendChild(el("span", "dim", " \u2116reversed"));
    tr.appendChild(idc);
    tr.appendChild(el("td", null, (r.label || "") + " " + shortId(r.provider_peer_id)));
    tr.appendChild(el("td", "r num", fmt(r.amount, 4)));
    tr.appendChild(el("td", "r num " + (r.slash_amount ? "bad" : "dim"), fmt(r.slash_amount, 4)));
    const st = el("td");
    const cls = r.reversed ? "hold"
      : r.state === "settled" ? "settled" : r.state === "slashed" ? "slashed" : "hold";
    st.appendChild(el("span", "pill " + cls,
      r.reversed ? "reversed"
        : r.state === "awaiting_verification" ? "escrowed" : r.state));
    tr.appendChild(st);
    body.appendChild(tr);
  });
}

function pushLog(ev) {
  const log = $("log");
  $("log-empty").style.display = "none";
  const level = ev.level === "error" || ev.level === "warn" ? "warn"
    : ev.type === "settlement" || ev.type === "verdict" ? "good" : "";
  const row = el("div", level);
  row.appendChild(el("span", "t", clock(ev.ts_ms)));
  row.appendChild(el("span", "ty", ev.type));
  row.appendChild(el("span", "m", describe(ev)));
  log.insertBefore(row, log.firstChild);
  while (log.childNodes.length > 200) log.removeChild(log.lastChild);
  $("events-count").textContent = "seq " + ev.seq;

  // The Overview page carries a short mirror of the same feed, so the landing
  // page shows the system is alive without holding the whole 200-row log.
  // The overview mirror skips the periodic roster ping: fourteen identical
  // "5 peers - ollama ok" lines say nothing about what the grid is doing.
  const routine = ev.type === "node" || ev.type === "heartbeat" || ev.type === "roster";
  const mini = routine ? null : $("log-mini");
  if (mini) {
    const m = $("log-mini-empty");
    if (m) m.style.display = "none";
    mini.insertBefore(row.cloneNode(true), mini.firstChild);
    while (mini.childNodes.length > 14) mini.removeChild(mini.lastChild);
  }
  const nb = $("nav-events");
  if (nb) nb.textContent = ev.seq;
}

/* ------------------------------------------------------------------ router
 * Each section is its own page rather than another panel in a long column:
 * a roster with nine columns and a job feed with nine more do not belong on
 * one screen. Routing is by hash, so it needs no server, no build step and no
 * history API, and a link to a page can be shared or bookmarked.
 */
const PAGES = ["overview", "nodes", "jobs", "verification", "settlement", "console", "events"];

function currentPage() {
  const h = (location.hash || "#/").replace(/^#\/?/, "").split("?")[0];
  return PAGES.includes(h) ? h : "overview";
}

function route() {
  const page = currentPage();
  document.querySelectorAll("[data-page]").forEach((el_) => {
    el_.classList.toggle("on", el_.dataset.page === page);
  });
  document.querySelectorAll("[data-nav]").forEach((a) => {
    const on = a.dataset.nav === page;
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.title = "The Edge Grid \u2014 " + page.charAt(0).toUpperCase() + page.slice(1);
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", route);

function describe(ev) {
  switch (ev.type) {
    case "job.created": return (ev.job || {}).model + " · " + trunc(ev.question, 60);
    case "job.stage": return ev.stage + " " + ev.state + (ev.ms !== null && ev.ms !== undefined ? " " + ev.ms + "ms" : "");
    case "bid": return shortId(ev.bid.bidder_peer_id) + " " + fmt(ev.bid.price, 4) +
      " (eff " + fmt(ev.effective_price, 4) + (ev.bid.warm ? ", warm" : "") + ")";
    case "award": return "→ " + shortId(ev.award.winner_peer_id) + " clearing " + fmt(ev.award.clearing_price, 4) +
      " of " + ev.award.n_bids + " bids";
    case "inference.start": return "start on " + shortId(ev.peer_id) + " " + ev.model;
    case "inference.done": return "ttft " + Math.round(ev.result.ttft_ms) + "ms · " + ev.result.tokens_generated +
      " tok · " + fmt(ev.result.tokens_per_sec, 1) + " tok/s";
    case "commit": return "blob " + String(ev.commitment.blob_ref).slice(0, 12) + " h" + ev.commitment.blob_height +
      " proof " + (ev.da || {}).proof_len;
    case "verdict": return ev.verdict.verdict + " score " + (ev.verdict.quality_score ?? "—") +
      " via " + ev.verdict.judge_backend;
    case "settlement": return ev.settlement.state + " " + fmt(ev.settlement.amount, 4) + " GRID" +
      (ev.settlement.slash_amount ? " slash " + fmt(ev.settlement.slash_amount, 4) : "");
    case "node": return (ev.nodes || []).length + " peers · ollama " + (ev.ollama_ok ? "ok" : "down");
    case "log": return ev.message;
    default: return JSON.stringify(ev).slice(0, 120);
  }
}

function renderFootnote() {
  const h = S.health || {};
  const f = $("footnote");
  f.innerHTML = "";
  f.appendChild(el("b", null, "mode " + (S.mode || "?") + " — "));
  f.appendChild(document.createTextNode(S.modeReason || "waiting for /health."));
  if (S.mode === "local") {
    f.appendChild(el("br"));
    f.appendChild(document.createTextNode(
      "identities, signatures, the second-price auction, ttft and token counts, the DA merkle proof, " +
      "the judge call and the stake arithmetic are all real. there is one machine, so whichever node " +
      "wins, the tokens are produced by this host's runtime and attributed to the winner — every job " +
      "record carries execution.attributed_to_winner and the event stream logs it. no result here is " +
      "a network measurement."));
  }
  if (h.judge) {
    f.appendChild(el("br"));
    f.appendChild(document.createTextNode(
      "judge: " + h.judge.backend + " / " + h.judge.model + ", pass threshold " + h.judge.pass_threshold +
      ", groq key " + (h.judge.groq_key_set ? "set" : "not set") +
      ". a judge outage is recorded as verdict=error, never as a pass or a fail."));
  }
}

/* -------------------------------------------------------------- console */

$("clear").onclick = () => {
  $("out").className = "out";
  $("out").innerHTML = "<span class='dim'>stream output appears here. tokens arrive over SSE from the winning node.</span>";
  $("out-meta").innerHTML = "";
};

$("send").onclick = async () => {
  if (S.streaming) return;
  const prompt = $("prompt").value.trim();
  if (!prompt) return;
  const model = $("model").value;
  const out = $("out");
  const meta = $("out-meta");
  out.className = "out";
  out.textContent = "";
  meta.innerHTML = "";
  S.streaming = true;
  $("send").disabled = true;
  $("send-status").textContent = "auctioning…";

  const caret = el("span", "caret", "▮");
  out.appendChild(caret);

  try {
    const r = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: model || undefined,
        messages: [{ role: "user", content: prompt }],
        stream: true,
        max_tokens: Number($("maxtok").value) || 120,
        verify: $("force").checked,
      }),
    });
    if (!r.ok) {
      const body = await r.text();
      throw new Error("HTTP " + r.status + " " + body.slice(0, 300));
    }
    $("send-status").textContent = "streaming (mode " + (r.headers.get("x-edgegrid-mode") || "?") + ")";
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let summary = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = frame.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const data = line.slice(6);
        if (data === "[DONE]") continue;
        const obj = JSON.parse(data);
        if (obj.error) {
          out.className = "out err";
          out.textContent = "pipeline error: " + obj.error.message;
          summary = null;
          continue;
        }
        const delta = ((obj.choices || [])[0] || {}).delta || {};
        if (delta.content) out.insertBefore(document.createTextNode(delta.content), caret);
        if (obj.edgegrid) summary = obj.edgegrid;
        if (obj.usage) summary = Object.assign(summary || {}, { usage: obj.usage });
        out.scrollTop = out.scrollHeight;
      }
    }
    caret.remove();
    if (summary) renderSummary(summary);
    $("send-status").textContent = "done";
  } catch (e) {
    caret.remove();
    out.className = "out err";
    out.textContent = "request failed: " + (e.message || e);
    $("send-status").textContent = "failed";
  } finally {
    S.streaming = false;
    $("send").disabled = false;
  }
};

function renderSummary(s) {
  const meta = $("out-meta");
  meta.innerHTML = "";
  const add = (k, v) => {
    if (v === undefined || v === null || v === "") return;
    meta.appendChild(el("dt", null, k));
    meta.appendChild(el("dd", null, v));
  };
  add("job", s.job_id);
  add("mode", s.mode);
  add("provider", nodeLabel(s.provider_peer_id) + " " + shortId(s.provider_peer_id));
  add("auction", s.n_bids + " bids · own " + fmt(s.winning_bid_grid, 4) +
      " → clearing " + fmt(s.clearing_price_grid, 4) + " GRID in " + fmt(s.auction_ms, 2) + "ms");
  add("ttft", Math.round(s.ttft_ms) + " ms · " + fmt(s.tokens_per_sec, 1) + " tok/s");
  if (s.usage) add("tokens", s.usage.prompt_tokens + " in / " + s.usage.completion_tokens + " out");
  if (s.da) add("da", "h" + s.da.height + " root " + String(s.da.root).slice(0, 20) + "… proof " + s.da.proof_len);
  add("output hash", String(s.output_hash || "").slice(0, 32) + "…");
  add("verify", s.sampled ? (s.verdict + " via " + s.judge_backend) : "not sampled");
  add("settlement", s.settlement_state);
  add("total", fmt(s.total_ms, 1) + " ms");
  (s.notes || []).forEach((n, i) => add(i === 0 ? "notes" : "", n));
  if (selected !== s.job_id) { selected = s.job_id; renderJobs(); renderAuction(); }
}

/* keep the nav counters in step with the panel counters they mirror */
function syncNav() {
  // Panel counters carry a sentence ("1 records - 1 live - 0.0179 GRID paid");
  // a tab can only hold the number, so take the first one.
  const firstNumber = (t) => {
    const m = String(t || "").match(/-?\d[\d,.]*/);
    return m ? m[0] : "0";
  };
  const pairs = [["nav-nodes", "nodes-count"], ["nav-jobs", "jobs-count"],
                 ["nav-ledger", "ledger-count"]];
  for (const [dst, src] of pairs) {
    const d = $(dst), o = $(src);
    if (d && o) d.textContent = firstNumber(o.textContent);
  }
  const v = $("nav-verif"), p = $("v-pass"), f = $("v-fail"), e = $("v-err");
  if (v && p && f && e) {
    v.textContent = (+p.textContent || 0) + (+f.textContent || 0) + (+e.textContent || 0);
  }
}
setInterval(syncNav, 1000);

route();
boot();
