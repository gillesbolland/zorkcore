/* ZorCore plugin UI — runtime + companion setup via Repeater APIs. */

const PLUGIN_ID = "zorcore";
const TCP_PORT = 1977;
const BIND = "127.0.0.1";
/** Same keys as OpenHop Repeater webadmin SPA. */
const JWT_STORAGE_KEY = "pymc_jwt_token";
const API_KEY_STORAGE_KEY = "zorcore_api_key";

let activeCompanionName = "Zork🕹️";
let lastRuntime = {};
let lastConfig = {};

const TAB_IDS = ["main", "safety", "dungeon", "radio", "bans"];
const TAB_STORAGE_KEY = "zorcore_ui_tab";

function showTab(tabId) {
  const id = TAB_IDS.includes(tabId) ? tabId : "main";
  for (const name of TAB_IDS) {
    const panel = document.getElementById(`tab-${name}`);
    const btn = document.getElementById(`tab-btn-${name}`);
    const on = name === id;
    if (panel) panel.hidden = !on;
    if (btn) {
      btn.setAttribute("aria-selected", on ? "true" : "false");
      btn.tabIndex = on ? 0 : -1;
    }
  }
  try {
    sessionStorage.setItem(TAB_STORAGE_KEY, id);
  } catch (_) {
    /* ignore */
  }
}

function wireTabs() {
  const nav = document.querySelector(".tabs");
  if (!nav) return;
  nav.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-tab]");
    if (!btn) return;
    showTab(btn.getAttribute("data-tab") || "main");
  });
  nav.addEventListener("keydown", (ev) => {
    const current = ev.target.closest("button[data-tab]");
    if (!current) return;
    const idx = TAB_IDS.indexOf(current.getAttribute("data-tab") || "");
    if (idx < 0) return;
    let next = -1;
    if (ev.key === "ArrowRight" || ev.key === "ArrowDown") next = (idx + 1) % TAB_IDS.length;
    if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") {
      next = (idx - 1 + TAB_IDS.length) % TAB_IDS.length;
    }
    if (ev.key === "Home") next = 0;
    if (ev.key === "End") next = TAB_IDS.length - 1;
    if (next < 0) return;
    ev.preventDefault();
    const btn = document.getElementById(`tab-btn-${TAB_IDS[next]}`);
    if (btn) {
      btn.focus();
      showTab(TAB_IDS[next]);
    }
  });
  let initial = "main";
  try {
    initial = sessionStorage.getItem(TAB_STORAGE_KEY) || "main";
  } catch (_) {
    /* ignore */
  }
  showTab(initial);
}

function companionName(runtime, config) {
  return (
    runtime.companion_name ||
    config.companion_name ||
    activeCompanionName ||
    "Zork🕹️"
  );
}

function authHeaders() {
  const headers = {};
  try {
    const jwt = localStorage.getItem(JWT_STORAGE_KEY);
    if (jwt) headers.Authorization = `Bearer ${jwt}`;
  } catch (_) {
    /* ignore private mode / blocked storage */
  }
  try {
    const apiKey =
      localStorage.getItem(API_KEY_STORAGE_KEY) ||
      sessionStorage.getItem(API_KEY_STORAGE_KEY);
    if (apiKey) headers["X-API-Key"] = apiKey;
  } catch (_) {
    /* ignore */
  }
  return headers;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      Accept: "application/json",
      ...authHeaders(),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = { raw: text };
  }
  if (res.status === 401 || res.status === 403) {
    const hasJwt = !!authHeaders().Authorization;
    const err = new Error(
      hasJwt
        ? "Auth rejected — log into the Repeater dashboard again, then reload this page."
        : "Auth required — open /plugins/zorcore/ from the same browser after logging into the Repeater dashboard (JWT is stored in localStorage)."
    );
    err.status = res.status;
    err.data = data;
    throw err;
  }
  if (!res.ok) {
    const msg =
      (data && (data.error || data.message || data.detail)) ||
      `HTTP ${res.status}`;
    const err = new Error(String(msg));
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

async function fetchRuntime() {
  try {
    const data = await api(`/api/plugins/runtime?id=${encodeURIComponent(PLUGIN_ID)}`);
    return data.runtime || data.data?.runtime || data;
  } catch (_) {
    return null;
  }
}

async function fetchPluginConfig() {
  try {
    const data = await api(`/api/plugins/settings?id=${encodeURIComponent(PLUGIN_ID)}`);
    const cfg = data.config || data.settings || data.data?.config || data.data || {};
    if (!cfg || typeof cfg !== "object") return {};
    const cleaned = { ...cfg };
    delete cleaned.success;
    delete cleaned.id;
    delete cleaned.path;
    delete cleaned.error;
    return cleaned;
  } catch (_) {
    return {};
  }
}

async function fetchIdentities() {
  const data = await api("/api/identities");
  return data.data || data;
}

function listConfigured(identities) {
  if (!identities) return [];
  const out = [];
  for (const key of [
    "configured",
    "configured_companions",
    "identities",
    "companions",
  ]) {
    const arr = identities[key];
    if (Array.isArray(arr)) out.push(...arr);
  }
  const seen = new Set();
  return out.filter((i) => {
    const n = String(i?.name || "").trim();
    const t = String(i?.type || i?.identity_type || "").trim();
    const k = `${t}:${n}`;
    if (!n || seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

function findIdentity(identities, name, type) {
  const items = listConfigured(identities);
  return items.find((i) => {
    const n = String(i.name || i.settings?.node_name || "").trim();
    const t = String(i.type || i.identity_type || "").trim();
    if (n !== name) return false;
    if (!type) return true;
    return !t || t === type;
  });
}

function findCompanion(identities, name) {
  return findIdentity(identities, name, "companion") || null;
}

function publicKeyOf(identity) {
  if (!identity) return "";
  return String(
    identity.public_key ||
      identity.public_key_full ||
      identity.pubkey ||
      identity.hash ||
      ""
  ).toLowerCase();
}

function setSoftMsg(id, text, isError) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text || "";
  el.style.color = isError ? "#f0b4a8" : "";
}

function drawCompanionQr(canvas, url) {
  const msg = document.getElementById("qr-msg");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!url) {
    ctx.fillStyle = "#999";
    ctx.font = "12px sans-serif";
    ctx.fillText("no meshcore:// URL", 16, canvas.height / 2);
    if (msg) msg.textContent = "Companion public key missing — create / link the companion.";
    return;
  }
  if (typeof QRCode === "undefined" || !QRCode.toCanvas) {
    ctx.fillStyle = "#999";
    ctx.font = "12px sans-serif";
    ctx.fillText("QR library missing", 16, canvas.height / 2);
    if (msg) msg.textContent = "qrcode.min.js failed to load.";
    return;
  }
  QRCode.toCanvas(
    canvas,
    url,
    {
      width: canvas.width,
      margin: 1,
      color: { dark: "#111111", light: "#ffffff" },
      errorCorrectionLevel: "M",
    },
    (err) => {
      if (err) {
        if (msg) msg.textContent = `QR failed: ${err.message || err}`;
        return;
      }
      if (msg) msg.textContent = "";
    }
  );
}

function setMeshLink(id, url) {
  const el = document.getElementById(id);
  if (!el) return;
  if (url) {
    el.textContent = url;
    el.setAttribute("href", url);
    el.title = "Open in MeshCore";
  } else {
    el.textContent = "—";
    el.setAttribute("href", "#");
    el.removeAttribute("title");
  }
}

function fillRegionSelect(runtime, config) {
  const sel = document.getElementById("region-scope");
  if (!sel) return;
  if (document.activeElement === sel) return;

  const current = String(runtime.region_scope ?? config.region_scope ?? "")
    .trim()
    .replace(/^#/, "");
  const regions = Array.isArray(runtime.available_regions)
    ? runtime.available_regions
        .map((r) => String(r || "").trim().replace(/^#/, ""))
        .filter(Boolean)
    : [];
  const seen = new Set();
  const options = [{ value: "", label: "Unscoped" }];
  for (const code of regions) {
    const key = code.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    options.push({ value: code, label: `#${code}` });
  }
  if (current && !seen.has(current.toLowerCase())) {
    options.push({ value: current, label: `#${current} (saved)` });
  }

  const prev = sel.value;
  sel.innerHTML = options
    .map(
      (o) =>
        `<option value="${String(o.value).replace(/"/g, "&quot;")}">${o.label}</option>`
    )
    .join("");
  const want = current || "";
  const hasWant = Array.from(sel.options).some((o) => o.value === want);
  sel.value = hasWant ? want : prev || "";

  const hint = document.getElementById("region-hint");
  if (hint) {
    hint.textContent = regions.length
      ? `${regions.length} region(s) from OpenHop transport keys`
      : "No transport keys loaded — showing Unscoped / saved only";
  }
}

function medalFor(rank) {
  if (rank === 0) return "🥇";
  if (rank === 1) return "🥈";
  if (rank === 2) return "🥉";
  return "";
}

function playerName(row) {
  const name = String(row.display_name || "").trim();
  if (name && !/^[0-9a-f]{8,}$/i.test(name)) return name;
  const key = row.pubkey || row.sender_key || "";
  return name || shortKey(key) || "—";
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value || "—";
}

function setMsg(text, isError) {
  const el = document.getElementById("setup-msg");
  el.textContent = text || "";
  el.style.color = isError ? "#f0b4a8" : "";
}

function setSafetyMsg(text, isError) {
  const el = document.getElementById("safety-msg");
  if (!el) return;
  el.textContent = text || "";
  el.style.color = isError ? "#f0b4a8" : "";
}

function setQueueMsg(text, isError) {
  const el = document.getElementById("queue-msg");
  if (!el) return;
  el.textContent = text || "";
  el.style.color = isError ? "#f0b4a8" : "";
}

function renderChips({ companion, linked, port, companionUp, name }) {
  const chips = document.getElementById("iface-chips");
  const ready = !!companion && !!linked;
  const items = [
    { label: `${name}: ${companion ? "present" : "missing"}`, ok: !!companion },
    { label: `Linked: ${linked ? "yes" : "no"}`, ok: !!linked },
    {
      label: `TCP ${BIND}:${port || TCP_PORT}${companionUp ? " · up" : " · down"}`,
      ok: !!companionUp,
    },
    {
      label: ready ? "Ready" : "Needs setup",
      ok: ready,
    },
  ];
  chips.innerHTML = items
    .map((c) => `<span class="chip ${c.ok ? "ok" : "bad"}">${c.label}</span>`)
    .join("");

  const btnCreate = document.getElementById("btn-create");
  const btnLink = document.getElementById("btn-link");
  const btnSetup = document.getElementById("btn-setup");
  if (btnCreate) {
    btnCreate.textContent = companion ? "Companion OK" : "Create companion";
    btnCreate.disabled = !!companion;
    btnCreate.title = companion
      ? "Companion identity already exists"
      : "Create the silent game companion identity in OpenHop";
  }
  if (btnLink) {
    btnLink.textContent = linked ? "Re-link companion" : "Link companion";
    btnLink.title =
      "Write the companion public key into plugin settings (restarts the plugin)";
  }
  if (btnSetup) {
    btnSetup.textContent = ready ? "Repair interfaces" : "Setup / repair";
    btnSetup.title = ready
      ? "Only needed if you deleted the companion in OpenHop or the link broke"
      : "Create the companion if missing, then link it to this plugin";
  }
}

function isLinked(runtime, config) {
  const adv =
    runtime.adventurer_public_key || config.adventurer_public_key || "";
  const port = Number(
    runtime.meshcore_port ?? config.meshcore_port ?? TCP_PORT
  );
  return Boolean(adv) && port === TCP_PORT;
}

function pathHashLabel(mode) {
  const m = Number(mode);
  if (m === 2) return "3-byte";
  if (m === 1) return "2-byte";
  return "1-byte";
}

function safetyFrom(runtime, config) {
  const s = runtime.safety || {};
  return {
    safety_enabled: s.safety_enabled ?? config.safety_enabled ?? true,
    bans_enabled: s.bans_enabled ?? config.bans_enabled ?? true,
    single_player_enabled:
      s.single_player_enabled ?? config.single_player_enabled ?? true,
    play_max_tier: s.play_max_tier ?? config.play_max_tier ?? "normal",
    quiet_hold_seconds: s.quiet_hold_seconds ?? config.quiet_hold_seconds ?? 120,
    quiet_poll_seconds: s.quiet_poll_seconds ?? config.quiet_poll_seconds ?? 30,
    active_grace_seconds:
      s.active_grace_seconds ?? config.active_grace_seconds ?? 1200,
    offer_timeout_seconds:
      s.offer_timeout_seconds ?? config.offer_timeout_seconds ?? 900,
    queue_max: s.queue_max ?? config.queue_max ?? 20,
    max_local_players: s.max_local_players ?? config.max_local_players ?? 2,
    daemons_enabled: s.daemons_enabled ?? config.daemons_enabled ?? true,
    daemon_idle_pause_seconds:
      s.daemon_idle_pause_seconds ?? config.daemon_idle_pause_seconds ?? 300,
    world_events_enabled:
      s.world_events_enabled ?? config.world_events_enabled ?? true,
    world_events_channel_enabled:
      s.world_events_channel_enabled ??
      config.world_events_channel_enabled ??
      false,
    world_events_channel_name:
      s.world_events_channel_name ?? config.world_events_channel_name ?? "",
    reply_settle_ms: s.reply_settle_ms ?? config.reply_settle_ms ?? 3500,
    inter_chunk_delay_ms:
      s.inter_chunk_delay_ms ?? config.inter_chunk_delay_ms ?? 800,
  };
}

let selectedPlayMaxTier = "normal";

function setTierSegment(tier) {
  selectedPlayMaxTier = tier || "normal";
  const root = document.getElementById("play-max-tier");
  if (!root) return;
  for (const btn of root.querySelectorAll("button[data-tier]")) {
    btn.classList.toggle("active", btn.getAttribute("data-tier") === selectedPlayMaxTier);
  }
}

function fillSafetyForm(runtime, config) {
  const s = safetyFrom(runtime, config);
  const ids = [
    "safety_enabled",
    "single_player_enabled",
    "bans_enabled",
    "daemons_enabled",
    "world_events_enabled",
    "world_events_channel_enabled",
  ];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.checked = !!s[id];
  }
  const nums = [
    "quiet_hold_seconds",
    "quiet_poll_seconds",
    "active_grace_seconds",
    "offer_timeout_seconds",
    "queue_max",
    "max_local_players",
    "daemon_idle_pause_seconds",
    "reply_settle_ms",
    "inter_chunk_delay_ms",
  ];
  for (const id of nums) {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.value = String(s[id]);
  }
  const chanName = document.getElementById("world_events_channel_name");
  if (chanName && document.activeElement !== chanName) {
    chanName.value = s.world_events_channel_name || "";
  }
  setTierSegment(s.play_max_tier);
}

function hopsLabel(row) {
  const n = row.path_len;
  if (n == null || n < 0 || n >= 255) return "flood/?";
  return String(n);
}

function qualityBadges(row) {
  let html = "";
  if (row.local) html += `<span class="badge ok">local</span>`;
  if (row.stable) html += `<span class="badge ok">stable</span>`;
  else if (row.local) html += `<span class="badge warn">unstable</span>`;
  return html;
}

function shortKey(key) {
  const k = String(key || "");
  return k.length > 16 ? `${k.slice(0, 16)}…` : k || "—";
}

function fmtTime(ts) {
  if (!ts) return "—";
  try {
    return new Date(Number(ts) * 1000).toLocaleString();
  } catch (_) {
    return "—";
  }
}

function actionButtons(pubkey, name) {
  const esc = String(pubkey || "").replace(/"/g, "");
  const n = String(name || "").replace(/"/g, "&quot;");
  return `
    <button type="button" data-op="promote" data-pubkey="${esc}">Promote</button>
    <button type="button" data-op="drop_queue" data-pubkey="${esc}">Drop</button>
    <button type="button" data-op="reset_session" data-pubkey="${esc}">Reset</button>
    <button type="button" class="danger" data-op="ban" data-pubkey="${esc}" data-name="${n}">Ban</button>
  `;
}

function fmtCountdown(seconds) {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function renderDungeon(runtime) {
  const dungeon = runtime.dungeon || {};
  const npcs = dungeon.npcs || [];
  const box = document.getElementById("dungeon-box");
  if (box) {
    if (!npcs.length) {
      box.textContent = "This adventure has no wandering NPCs.";
    } else {
      const clock = dungeon.clock_paused ? "paused (nobody playing)" : "running";
      box.textContent = `${dungeon.content || "—"} · clock ${clock} · last activity ${fmtTime(
        dungeon.last_player_at
      )}`;
    }
  }
  const body = document.getElementById("dungeon-body");
  if (!body) return;
  body.innerHTML = npcs.length
    ? npcs
        .map(
          (n) => `<tr>
            <td>${n.name || n.id}</td>
            <td class="mono small">${n.room || "—"}</td>
            <td>${
              n.alive
                ? '<span class="badge ok">alive</span>'
                : '<span class="badge warn">slain</span>'
            }</td>
            <td>${n.stash || 0}</td>
            <td>${n.alive ? "—" : fmtCountdown(n.respawn_in)}</td>
          </tr>`
        )
        .join("")
    : `<tr><td colspan="5">—</td></tr>`;
}

function renderAccess(runtime) {
  const access = runtime.access || {};
  const active = access.active || runtime.active_player;
  const box = document.getElementById("active-box");
  if (box) {
    if (!active) {
      const offer = access.offer || runtime.offers;
      box.textContent = offer
        ? `Offer outstanding → ${offer.display_name || shortKey(offer.pubkey)} (expires ${fmtTime(offer.expires_at)})`
        : "No active player";
    } else {
      const actives =
        access.actives && access.actives.length
          ? access.actives
          : runtime.active_players && runtime.active_players.length
          ? runtime.active_players
          : [active];
      box.innerHTML = actives
        .map((a) => {
          const paused = a.paused ? " · PAUSED" : "";
          const local = a.local_exception || a.local ? " · local" : "";
          return `<div style="margin:0 0 0.5rem">Playing: ${
            a.display_name || shortKey(a.pubkey)
          } (${shortKey(a.pubkey)})${paused}${local} · hops ${hopsLabel(
            a
          )}${qualityBadges(a)} since ${fmtTime(
            a.since
          )}<div class="actions" style="margin:0.35rem 0 0">${actionButtons(
            a.pubkey,
            a.display_name
          )}</div></div>`;
        })
        .join("");
    }
  }

  const queue = access.queue || runtime.queue || [];
  const qbody = document.getElementById("queue-body");
  if (qbody) {
    qbody.innerHTML = queue.length
      ? queue
          .map(
            (q, i) => `<tr>
              <td>${i + 1}</td>
              <td>${q.display_name || shortKey(q.pubkey)}${qualityBadges(q)}</td>
              <td class="mono small">${shortKey(q.pubkey)}</td>
              <td>${hopsLabel(q)}</td>
              <td>${fmtTime(q.enqueued_at)}</td>
              <td>${actionButtons(q.pubkey, q.display_name)}</td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="6">Queue empty</td></tr>`;
  }

  const bans = access.bans || runtime.bans || [];
  const bbody = document.getElementById("bans-body");
  if (bbody) {
    bbody.innerHTML = bans.length
      ? bans
          .map(
            (b) => `<tr>
              <td>${b.display_name || shortKey(b.pubkey)}</td>
              <td class="mono small">${shortKey(b.pubkey)}</td>
              <td>${b.reason || ""}</td>
              <td><button type="button" data-op="unban" data-pubkey="${b.pubkey}">Unban</button></td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="4">No bans</td></tr>`;
  }

  renderLeaderboard(runtime);
}

function renderLeaderboard(runtime) {
  const lb = runtime.airtime_leaderboard || runtime.leaderboard || [];
  const players = runtime.players || [];
  const byShort = {};
  for (const p of players) {
    const k = String(p.pubkey || p.sender_key || "").slice(0, 16);
    if (k) byShort[k] = p;
  }
  const merged = lb.map((r) => {
    const short = String(r.pubkey || r.sender_key || "").slice(0, 16);
    const p = byShort[short] || {};
    return {
      ...p,
      ...r,
      display_name: r.display_name || p.display_name,
      room_id: r.room_id || p.room_id || "",
      last_send_failed:
        r.last_send_failed != null ? r.last_send_failed : p.last_send_failed,
    };
  });
  // Include session-only players missing from airtime.
  for (const p of players) {
    const short = String(p.pubkey || p.sender_key || "").slice(0, 16);
    if (!merged.some((r) => String(r.pubkey || r.sender_key || "").slice(0, 16) === short)) {
      merged.push(p);
    }
  }
  merged.sort(
    (a, b) =>
      (b.score || 0) - (a.score || 0) || (b.bytes_out || 0) - (a.bytes_out || 0)
  );

  const lbody = document.getElementById("leaderboard-body");
  if (!lbody) return;
  lbody.innerHTML = merged.length
    ? merged
        .map((r, i) => {
          const medal = medalFor(i);
          return `<tr>
              <td class="medal">${medal}</td>
              <td>${playerName(r)}<div class="mono small">${shortKey(
            r.pubkey || r.sender_key
          )}</div></td>
              <td>${r.score ?? 0}</td>
              <td>${r.moves ?? 0}</td>
              <td class="mono small">${r.room_id || "—"}</td>
              <td>${r.alive === false ? "no" : "yes"}</td>
              <td>${r.bytes_out ?? 0}</td>
              <td>${r.parts_out ?? 0}</td>
              <td>${r.airtime_units ?? Math.floor((r.bytes_out || 0) / 50)}</td>
              <td>${r.last_send_failed ? "failed" : "ok"}</td>
              <td>${fmtTime(r.last_seen || r.updated_at)}</td>
            </tr>`;
        })
        .join("")
    : `<tr><td colspan="11">No players yet</td></tr>`;
}

function renderQuietChip(runtime) {
  const chip = document.getElementById("quiet-chip");
  const detail = document.getElementById("quiet-detail");
  const arlWarn = document.getElementById("arl-warn");
  const q = runtime.quiet || {};
  const safety = runtime.safety || {};
  if (!chip) return;
  const tier = (q.advert_tier || "—").toLowerCase();
  const maxTier = q.play_max_tier || safety.play_max_tier || "normal";
  const arlOff =
    safety.safety_enabled !== false &&
    (q.adaptive_enabled === false ||
      q.reason === "arl_disabled" ||
      q.reason === "arl_unavailable");

  if (arlWarn) {
    if (arlOff) {
      arlWarn.hidden = false;
      arlWarn.textContent =
        q.reason === "arl_unavailable"
          ? "Adaptive Rate Limiting unavailable — check Repeater API / advert_rate_limit_stats. Remote play stays closed."
          : "ARL off — enable Adaptive Rate Limiting on this Repeater (repeater.advert_adaptive). Remote play stays closed until it is on.";
    } else {
      arlWarn.hidden = true;
      arlWarn.textContent = "";
    }
  }

  if (safety.safety_enabled === false) {
    chip.textContent = "Safety off";
    chip.className = "status-chip";
  } else if (arlOff) {
    chip.textContent =
      q.reason === "arl_unavailable" ? "ARL unavailable" : "ARL off";
    chip.className = "status-chip busy";
  } else if (q.is_quiet || q.is_open) {
    chip.textContent = `Open · ${tier}`;
    chip.className = "status-chip quiet";
  } else {
    chip.textContent = `Closed · ${tier} (local ≤3 ok)`;
    chip.className = "status-chip busy";
  }
  if (detail) {
    const util =
      q.utilization_percent == null
        ? "?"
        : `${Number(q.utilization_percent).toFixed(1)}%`;
    const arl =
      q.adaptive_enabled === true
        ? "ARL on"
        : q.adaptive_enabled === false
        ? "ARL off"
        : "ARL ?";
    const ewma =
      q.adverts_per_min_ewma == null
        ? ""
        : ` · ewma ${Number(q.adverts_per_min_ewma).toFixed(2)} adv/min`;
    detail.textContent = `${arl} · tier ${tier} · max ${maxTier} · util ${util} (telemetry)${ewma} · ${
      q.reason || "—"
    }`;
  }
}

function render(runtime, identities, config = {}) {
  lastRuntime = runtime || {};
  lastConfig = config || {};
  const port = runtime.meshcore_port ?? config.meshcore_port ?? TCP_PORT;
  const host = runtime.meshcore_host || config.meshcore_host || BIND;
  const advKey =
    runtime.adventurer_public_key || config.adventurer_public_key || "";
  const name = companionName(runtime, config);
  activeCompanionName = name;
  const advUrl =
    runtime.adventurer_url ||
    (advKey
      ? `meshcore://contact/add?name=${encodeURIComponent(name)}&public_key=${advKey}&type=1`
      : "");
  const region = runtime.region_scope ?? config.region_scope ?? "";
  const pathMode = runtime.path_hash_mode ?? config.path_hash_mode ?? 2;
  fillRegionSelect(runtime, config);

  fillSafetyForm(runtime, config);
  renderQuietChip(runtime);
  renderAccess(runtime);
  renderDungeon(runtime);

  const joinTitle = document.getElementById("join-title");
  if (joinTitle) joinTitle.textContent = `${name} (game)`;

  const dl = document.getElementById("status-dl");
  const advertsSeen = runtime.adverts_seen ?? runtime.stats?.adverts_seen ?? 0;
  const rows = [
    ["companion", runtime.companion_connected ? "connected" : "disconnected"],
    ["name", name],
    [
      "content",
      `${runtime.content_id || "zork-full"} @ ${
        runtime.content_version || "?"
      }`,
    ],
    ["tcp", `${host}:${port}`],
    ["advert_mode", runtime.advert_mode || "silent"],
    ["path_hash", pathHashLabel(pathMode)],
    ["region_scope", region || "(unscoped)"],
    ["adverts_seen", String(advertsSeen)],
    [
      "contacts_imported",
      String(runtime.contacts_imported ?? runtime.stats?.contacts_imported ?? 0),
    ],
    [
      "contact_sync",
      runtime.contact_sync_error || runtime.stats?.contact_sync_error || "ok",
    ],
    [
      "sync_window",
      `${runtime.advert_sync_hours ?? config.advert_sync_hours ?? 6}h / ${
        runtime.advert_sync_limit ?? config.advert_sync_limit ?? 20
      }`,
    ],
    ["sessions", String(runtime.session_count ?? 0)],
    [
      "heartbeat",
      runtime.heartbeat_at
        ? new Date(runtime.heartbeat_at * 1000).toLocaleString()
        : "—",
    ],
  ];
  dl.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");

  setText("adv-key", advKey || "—");
  setMeshLink("adv-url", advUrl);
  drawCompanionQr(document.getElementById("adv-qr"), advUrl);

  const companion = findCompanion(identities, name);
  const linked = isLinked(runtime, config);
  renderChips({
    companion: !!companion || !!advKey,
    linked,
    port,
    companionUp: !!runtime.companion_connected,
    name,
  });
}

async function createMissingIdentities() {
  const name = activeCompanionName;
  const identities = await fetchIdentities();
  if (findCompanion(identities, name)) {
    return `${name} already present`;
  }
  await api("/api/create_identity", {
    method: "POST",
    body: JSON.stringify({
      name,
      type: "companion",
      settings: {
        node_name: name,
        tcp_port: TCP_PORT,
        bind_address: BIND,
      },
    }),
  });
  return `Created ${name} companion`;
}

async function linkToPlugin() {
  const name = activeCompanionName;
  const identities = await fetchIdentities();
  const companion = findCompanion(identities, name);
  if (!companion) {
    throw new Error(`Create ${name} first.`);
  }
  const advKey = publicKeyOf(companion);
  if (!advKey) {
    throw new Error(
      `${name} exists but public key is missing — wait for registration / restart Repeater.`
    );
  }

  const cfg = await fetchPluginConfig();
  const region = (
    document.getElementById("region-scope")?.value ||
    ""
  )
    .trim()
    .replace(/^#/, "");

  Object.assign(cfg, {
    meshcore_host: BIND,
    meshcore_port: TCP_PORT,
    adventurer_public_key: advKey,
    companion_advert_enabled: false,
    companion_advert_local: false,
    companion_advert_flood: false,
    advert_sync_hours: cfg.advert_sync_hours ?? 6,
    advert_sync_limit: cfg.advert_sync_limit ?? 20,
    path_hash_mode: 2,
    region_scope: region,
  });

  await api("/api/plugins/settings", {
    method: "POST",
    body: JSON.stringify({ id: PLUGIN_ID, config: cfg, restart: true }),
  });
  return `Linked plugin settings and requested restart. ${name} stays silent — share meshcore:// URL privately.`;
}

async function saveSafetySettings() {
  const cfg = await fetchPluginConfig();
  cfg.safety_enabled = document.getElementById("safety_enabled").checked;
  cfg.single_player_enabled = document.getElementById(
    "single_player_enabled"
  ).checked;
  cfg.bans_enabled = document.getElementById("bans_enabled").checked;
  cfg.play_max_tier = selectedPlayMaxTier || "normal";
  cfg.quiet_hold_seconds = Number(
    document.getElementById("quiet_hold_seconds").value
  );
  cfg.quiet_poll_seconds = Number(
    document.getElementById("quiet_poll_seconds").value
  );
  cfg.active_grace_seconds = Number(
    document.getElementById("active_grace_seconds").value
  );
  cfg.offer_timeout_seconds = Number(
    document.getElementById("offer_timeout_seconds").value
  );
  cfg.queue_max = Number(document.getElementById("queue_max").value);
  cfg.max_local_players = Number(
    document.getElementById("max_local_players").value
  );
  cfg.daemons_enabled = document.getElementById("daemons_enabled").checked;
  cfg.world_events_enabled = document.getElementById(
    "world_events_enabled"
  ).checked;
  cfg.world_events_channel_enabled = document.getElementById(
    "world_events_channel_enabled"
  ).checked;
  cfg.world_events_channel_name = (
    document.getElementById("world_events_channel_name").value || ""
  )
    .trim()
    .replace(/^#/, "");
  delete cfg.world_events_channel_index;
  cfg.daemon_idle_pause_seconds = Number(
    document.getElementById("daemon_idle_pause_seconds").value
  );
  cfg.reply_settle_ms = Number(document.getElementById("reply_settle_ms").value);
  cfg.inter_chunk_delay_ms = Number(
    document.getElementById("inter_chunk_delay_ms").value
  );
  delete cfg.admin_actions;
  delete cfg.quiet_max_utilization_percent;
  delete cfg.quiet_require_advert_tier;
  await api("/api/plugins/settings", {
    method: "POST",
    body: JSON.stringify({ id: PLUGIN_ID, config: cfg, restart: false }),
  });
  return "Safety settings saved (no restart).";
}

async function postAdminActions(actions) {
  const cfg = await fetchPluginConfig();
  cfg.admin_actions = actions;
  await api("/api/plugins/settings", {
    method: "POST",
    body: JSON.stringify({ id: PLUGIN_ID, config: cfg, restart: false }),
  });
}

async function refreshAll() {
  let identities = null;
  let config = {};
  try {
    identities = await fetchIdentities();
  } catch (e) {
    if (e.status === 401 || e.status === 403) {
      setMsg(e.message, true);
    }
  }
  try {
    config = await fetchPluginConfig();
  } catch (_) {
    config = {};
  }
  const runtime = (await fetchRuntime()) || {};
  render(runtime, identities || {}, config);
}


async function saveRegionScope() {
  const cfg = await fetchPluginConfig();
  const code = (document.getElementById("region-scope").value || "")
    .trim()
    .replace(/^#/, "");
  cfg.region_scope = code;
  cfg.admin_actions = [{ op: "apply_radio_policy" }];
  await api("/api/plugins/settings", {
    method: "POST",
    body: JSON.stringify({ id: PLUGIN_ID, config: cfg, restart: false }),
  });
  return code
    ? `Region scope set to ${code} (applied to companion).`
    : "Region scope cleared — companion is unscoped.";
}

async function requestAdvert(flood) {
  const cfg = await fetchPluginConfig();
  // One-shot only — never leave auto-advert enabled.
  cfg.companion_advert_enabled = false;
  cfg.companion_advert_local = false;
  cfg.companion_advert_flood = false;
  cfg.admin_actions = [{ op: "send_advert", flood: !!flood }];
  await api("/api/plugins/settings", {
    method: "POST",
    body: JSON.stringify({ id: PLUGIN_ID, config: cfg, restart: false }),
  });
  return flood
    ? "Flood advert requested — companion announced mesh-wide (stays silent after)."
    : "Local advert requested — companion announced nearby (stays silent after).";
}

function wireButtons() {
  const busy = (on) => {
    for (const id of [
      "btn-create",
      "btn-link",
      "btn-setup",
      "btn-safety",
      "btn-clear-queue",
      "btn-reset-npcs",
      "btn-region",
      "btn-advert-local",
      "btn-advert-flood",
    ]) {
      const el = document.getElementById(id);
      if (el) el.disabled = on;
    }
  };

  const tierRoot = document.getElementById("play-max-tier");
  if (tierRoot) {
    tierRoot.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-tier]");
      if (!btn) return;
      setTierSegment(btn.getAttribute("data-tier"));
    });
  }

  const btnRegion = document.getElementById("btn-region");
  if (btnRegion) {
    btnRegion.onclick = async () => {
      busy(true);
      try {
        const msg = await saveRegionScope();
        setSoftMsg("region-msg", msg, false);
        setTimeout(refreshAll, 1200);
      } catch (e) {
        setSoftMsg("region-msg", e.message || String(e), true);
      } finally {
        busy(false);
      }
    };
  }

  const btnLocal = document.getElementById("btn-advert-local");
  if (btnLocal) {
    btnLocal.onclick = async () => {
      busy(true);
      try {
        const msg = await requestAdvert(false);
        setSoftMsg("advert-msg", msg, false);
        setTimeout(refreshAll, 1200);
      } catch (e) {
        setSoftMsg("advert-msg", e.message || String(e), true);
      } finally {
        busy(false);
      }
    };
  }

  const btnFlood = document.getElementById("btn-advert-flood");
  if (btnFlood) {
    btnFlood.onclick = async () => {
      busy(true);
      try {
        const msg = await requestAdvert(true);
        setSoftMsg("advert-msg", msg, false);
        setTimeout(refreshAll, 1200);
      } catch (e) {
        setSoftMsg("advert-msg", e.message || String(e), true);
      } finally {
        busy(false);
      }
    };
  }

  document.getElementById("btn-create").onclick = async () => {
    busy(true);
    try {
      const msg = await createMissingIdentities();
      setMsg(msg, false);
      await refreshAll();
    } catch (e) {
      setMsg(e.message || String(e), true);
    } finally {
      busy(false);
    }
  };

  document.getElementById("btn-link").onclick = async () => {
    busy(true);
    try {
      const msg = await linkToPlugin();
      setMsg(msg, false);
      await refreshAll();
    } catch (e) {
      setMsg(e.message || String(e), true);
    } finally {
      busy(false);
    }
  };

  document.getElementById("btn-setup").onclick = async () => {
    busy(true);
    try {
      const a = await createMissingIdentities();
      const b = await linkToPlugin();
      setMsg(`${a}. ${b}`, false);
      await refreshAll();
    } catch (e) {
      setMsg(e.message || String(e), true);
    } finally {
      busy(false);
    }
  };

  const btnSafety = document.getElementById("btn-safety");
  if (btnSafety) {
    btnSafety.onclick = async () => {
      busy(true);
      try {
        const msg = await saveSafetySettings();
        setSafetyMsg(msg, false);
        await refreshAll();
      } catch (e) {
        setSafetyMsg(e.message || String(e), true);
      } finally {
        busy(false);
      }
    };
  }

  const btnClear = document.getElementById("btn-clear-queue");
  if (btnClear) {
    btnClear.onclick = async () => {
      busy(true);
      try {
        await postAdminActions([{ op: "clear_queue" }]);
        setQueueMsg("Clear queue requested.", false);
        setTimeout(refreshAll, 1500);
      } catch (e) {
        setQueueMsg(e.message || String(e), true);
      } finally {
        busy(false);
      }
    };
  }

  const btnResetNpcs = document.getElementById("btn-reset-npcs");
  if (btnResetNpcs) {
    btnResetNpcs.onclick = async () => {
      busy(true);
      const msg = document.getElementById("dungeon-msg");
      try {
        await postAdminActions([{ op: "reset_npcs" }]);
        if (msg) msg.textContent = "Dungeon reset requested.";
        setTimeout(refreshAll, 1500);
      } catch (e) {
        if (msg) msg.textContent = e.message || String(e);
      } finally {
        busy(false);
      }
    };
  }

  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-op]");
    if (!btn) return;
    const op = btn.getAttribute("data-op");
    const pubkey = btn.getAttribute("data-pubkey") || "";
    const display_name = btn.getAttribute("data-name") || "";
    if (!op) return;
    const action = { op, pubkey, display_name };
    if (op === "ban") {
      action.reason = window.prompt("Ban reason?", "operator") || "operator";
    }
    try {
      btn.disabled = true;
      await postAdminActions([action]);
      setQueueMsg(`${op} requested for ${shortKey(pubkey)}`, false);
      setTimeout(refreshAll, 1500);
    } catch (e) {
      setQueueMsg(e.message || String(e), true);
    } finally {
      btn.disabled = false;
    }
  });
}

async function boot() {
  wireTabs();
  wireButtons();
  const runtime = await fetchRuntime();
  if (!runtime) {
    document.getElementById("status-dl").innerHTML =
      "<dt>runtime</dt><dd>unavailable (open via Repeater /plugins/zorcore/ while logged in)</dd>";
  }
  await refreshAll();
  setInterval(refreshAll, 5000);
}

boot();
