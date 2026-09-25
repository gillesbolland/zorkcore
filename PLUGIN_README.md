# ZorCore — OpenHop Repeater plugin

In-process OpenHop plugin that runs a MeshCore text adventure: the full 1977 Zork dungeon baked into [`zorcore/content/`](zorcore/content/). Players DM the silent companion (name from content meta, always ending in 🕹️). Recent **Chat Node** adverts from OpenHop drive **import-only** companion contact import (missing keys are added; contacts are never mass-pruned when absent from the freshest-N advert window). Runtime exposes `adverts_seen`, `contacts_imported`, `contact_sync_error`, and `unresolved_senders` when DMs arrive before a contact exists.

Plugin id: `zorcore` · distribution: `zorcore-plugin` · console script: `zorcore`

Adventure data is a single fixed pack under [`zorcore/content/`](zorcore/content/) (144 rooms, 155 objects, 23 treasures) converted from the 1977 MIT MDL dungeon. Historical MDL sources are **not** in this repository — see [MITDDC/zork](https://github.com/MITDDC/zork). Re-run conversion with [`tools/mdl_to_content.py`](tools/mdl_to_content.py) against a local MITDDC checkout. Later games are expected to fork this plugin project rather than hot-swap content packs.

Room structure follows the 1977 dungeon data; **player-facing prose is the original MDL wording** (LOOK overlays for empty LDESC rooms). Long looks are split across MeshCore messages (`max_chunk_bytes` 145, `max_chunks` up to 16). It is not a Z-machine and does not embed MDL sources in the wheel.

Vehicles that the original handled in MDL code (the well bucket, the balloon, the mine cage, the shrinking cake) are expressed as `teleport` scripts, so every one of the 144 rooms is reachable.

Publish wheels from this dedicated plugin GitHub repository (Release assets). Catalogue `repository` must point here, not at the MDL archive.

Contract: [openHop Plugin Development](https://docs.openhop.dev/projects/openhop-repeater/plugin-development/). Catalogue approval is maintainer-reviewed ([plugin catalogue](https://github.com/openhop-dev/openhop-plugin-catalogue)); producers prepare Release wheels + metadata and cannot self-approve.

## Quiet by default

Defaults ship with **no companion advert** — the game companion never auto-adverts. One-shot **Local** / **Flood** adverts are manual from the webadmin Join path. Path hash mode defaults to **3-byte** (`path_hash_mode: 2`). Optional `region_scope` is chosen from OpenHop transport keys in the webadmin region picker.

### Setup (webadmin)

1. Install/enable the plugin on the Repeater.
2. Open `/plugins/zorcore/` while logged into the dashboard.
3. Optionally set **Region scope**, then **Setup game interfaces** (silent companion on `127.0.0.1:1977`).
4. Share the companion `meshcore://` URL privately.

### Player path

1. **Advert as a Chat Node** before the first DM so the plugin can import the phone’s pubkey. Once is enough before you start — you do not need to advert again for every command or every `new game`. If there is no reply, advert once more (the first one may have been too old, or never heard). The plugin imports freshest Chat Node pubkeys (default 6h / top 20).
2. Player adds the companion via the plugin URL/QR.
3. DM `new game` (also `new`, `start`, `play`, …). Other first messages (including `hello` / `?`) get short help. In-game `?` / `hello?` / `hello` **resend** the last reply. `again` / `g` / `repeat` **redo** the last gameplay command. Timed hazards use **⏱️** (no pause/resume).

### Radio-friendly commands

| Pattern | Example |
| --- | --- |
| Batch take/drop/put | `take all`, `take all except lamp`, `drop valuables` |
| Object lists | `take lamp, sword and paper` |
| Pronouns | `examine it`, `drop them` (from last successful object act) |
| LOOK UNDER | `look under rug` / `look under leaves` (puzzle lines); behind/through stub |
| Checkpoint | `save` → `Saved.` · `restore` → load slot or `No saved game.` |
| Leave play slot | `quit` / `q` / `bye` (checkpoint kept for later `restore`) |

### Reading a chunked reply

A **story** reply longer than one radio message ends with a trailing part label so the player knows when to answer:

| Mark | Meaning |
| --- | --- |
| `1/3…` | more story parts are coming — wait |
| `.3/3` | the story reply is complete |

A single-part story carries no mark. The **Exits** tip (and other status lines) are sent as a separate unlabeled message listing only directions that are **currently open** for that player — they are not part of the `n/m` count.


## Quiet-time gate + single-player queue

Play is gated by **OpenHop Adaptive Rate Limiting** advert tiers (`quiet` → `congested`), not clock time and not a utilization percentage. The Repeater running this plugin **must have Adaptive Rate Limiting enabled** (`repeater.advert_adaptive.enabled`). ZorCore reads `GET /api/advert_rate_limit_stats` and will not open remote play while `adaptive.enabled` is false (webadmin shows an ARL warning). Utilization from `/api/stats` is telemetry only.

- Setting `play_max_tier` (`quiet` | `normal` | `busy`, default **normal**) — play when the ARL `current_tier` is at or below that max.
- Must stay within max for `quiet_hold_seconds` (default 120) before opening; exceeding max closes immediately for **new** remote play.
- **Active grace** — `active_grace_seconds` (default **1200** / 20 min): remotes that already hold an active slot and commanded within this window keep playing through busy mesh flaps (no demotion; commands still run). Stale or brand-new remotes stay blocked until quiet returns. Locals (path ≤ `local_max_hops`, or on the sticky local list) keep the existing busy-local fast-pass.
- **Busy-local exception:** when the mesh is above `play_max_tier`, players with companion path ≤ `local_max_hops` (default **3**) may still play; flood/unknown paths stay local if previously sticky. Queue offers prefer **stable** locals (known path, no recent ACK fail / path reset).
- **Sticky locals** — a short live path adds the pubkey to `access.json` `local_players`; sticky ∪ path decides local for concurrency and busy-local. No auto-expiry; webadmin Access can Forget an entry.
- **Active idle timeout** — `active_idle_seconds` (default **3600** / 1 h; range 300–604800) frees active slots for local and remote players who stop commanding (session/checkpoint kept, same as quit). Distinct from `active_grace_seconds` and `daemon_idle_pause_seconds`.
- With `single_player_enabled`, remotes take turns through a FIFO queue (`offer_timeout_seconds`, default 900 / 15 minutes); locals share up to `max_local_players`.
- Approximate airtime: sum of UTF-8 bytes on successful outbound parts (webadmin shows bytes / parts / `bytes÷50` units).
- TX pacing defaults favour the repeater: `reply_settle_ms` **3500**, `inter_chunk_delay_ms` **800**.
- State persists in `$OPENHOP_PLUGIN_DATA/access.json`. Master switch: `safety_enabled` (false = legacy multi-session; ban list still applies if `bans_enabled`).
- Webadmin can promote/drop/ban/reset/clear, forget sticky locals, and reset the dungeon via `admin_actions` on settings save with `restart: false`.
- Legacy keys `quiet_max_utilization_percent` / `quiet_require_advert_tier` are ignored. The plugin does **not** auto-enable ARL on the Repeater.

## Shared living dungeon

The dungeon is **one world**, not one per player. The troll, cyclops and thief have a single position, a single life, and a single stash, held in `$OPENHOP_PLUGIN_DATA/npcs.json` and owned by the plugin. Two friends playing at the same time meet the same thief and can warn each other by voice.

- **Concurrent local play** — `max_local_players` (default **2**, range **1–16**) sticky/path-local players share the dungeon at once. Deeper and never-sticky flood-path players still take turns through the FIFO queue, because their traffic crosses the whole mesh.
- **NPC daemons** — the thief wanders, steals treasure (never ordinary gear) and hoards it; the troll swings; the cyclops gets hungry. `scope: "world"` daemons act on shared state, `scope: "player"` daemons act on one session. Minimum spacing is `daemon_min_interval_seconds` (default 60).
- **Idle pause** — with nobody playing for `daemon_idle_pause_seconds` (default **300**), the dungeon clock freezes rather than running for an empty room. When a player DMs again the pause is measured and every daemon deadline is shifted by the same amount, so returning does not trigger a burst of backlogged events.
- **Respawn** — a slain troll or cyclops returns after `respawn_seconds` (30 min). The thief returns only once his stash is empty, so his loot is always recoverable.
- **World events** — a kill broadcasts one short message to the other players currently in the dungeon ("Far below, a troll shrieks once, and the caverns swallow the sound."). Broadcasts are one message, never chunked, rate-limited by `broadcast_min_interval_seconds` (default 30), skipped for paused or queued players, and disabled entirely with `world_events_enabled: false`. Optionally mirror kills to a MeshCore autochannel with `world_events_channel_enabled` (default **false**) and `world_events_channel_name` (e.g. `zork` → `#zork`). With the channel flag alone (DM fan-out not required), a new game also publishes `@Name has entered the dungeon.` (pubkey prefix if the name is empty). Channel publish uses airtime and stays opt-in. Legacy `world_events_channel_index` is ignored.
- The webadmin **Dungeon** panel shows each NPC's room, life, stash size and respawn countdown, plus a **Reset dungeon** button.

## Adventure voice

Safety strings (busy, paused, resume, wait-turn, offer, banned, queue-full) are engine defaults that content may override in `meta.json` under `system_messages`, so the radio speaks in the game's own register rather than in network jargon. Congested mesh: *"A storm of voices rises above the dungeon. Your lamp gutters while you wait. I will call when it quiets."*

An `offer` override must keep its `{mins}` placeholder or it is rejected at load. Every override is checked by the test suite to fit a single radio message.

## Game content

Adventure data lives under [`zorcore/content/`](zorcore/content/) (fixed; not hot-swappable):

| File | Role |
| --- | --- |
| `meta.json` | id, name, version, companion_name, welcome/help, flags, dark policy, npcs, system_messages |
| `world.json` | rooms, objects, exits (including conditional / blocked) |
| `verbs.json` | directions, synonyms, start/resend phrases |
| `scripts.json` | declarative puzzle hooks, and the `daemons` list for living-world ticks |

The companion radio name comes from `meta.json` (joystick emoji enforced). Changing content files requires rebuilding/reinstalling the plugin; players may need `new game` if the content id/version stamp changes.

## Requirements

| Item | Detail |
| --- | --- |
| Python | `>=3.10` (plugin venv created by the manager) |
| Wheel dependency | `meshcore>=2.3.8` — catalogue SHA-256 covers the **wheel only**, not transitive pip resolves on rebuild |
| Companion | TCP `meshcore_host`:`meshcore_port` (default `127.0.0.1:1977`) |
| Repeater API | `repeater_api_base` (default `http://127.0.0.1:8000`) for advert sync and Adaptive Rate Limiting stats |
| Adaptive Rate Limiting | **Required** on the host Repeater (`repeater.advert_adaptive.enabled`). Without it, remote play stays closed. |
| Credentials | Prefer env `OPENHOP_REPEATER_TOKEN` or `REPEATER_API_TOKEN`; optional fallback `repeater_api_token` in config. Never put secrets in the wheel or UI assets. There is **no** injected Repeater token. |
| Data | All mutable state under `$OPENHOP_PLUGIN_DATA` (`config.json`, `runtime.json`, `access.json`, `npcs.json`, `sessions/`, `checkpoints/`). Upgrades keep data; default uninstall retains data. No automatic rollback. |

## Settings (defaults)

```json
{
  "meshcore_host": "127.0.0.1",
  "meshcore_port": 1977,
  "companion_advert_enabled": false,
  "path_hash_mode": 2,
  "region_scope": "",
  "advert_sync_hours": 6,
  "advert_sync_limit": 20,
  "reply_settle_ms": 3500,
  "command_dedupe_seconds": 20,
  "safety_enabled": true,
  "bans_enabled": true,
  "play_max_tier": "normal",
  "quiet_hold_seconds": 120,
  "quiet_poll_seconds": 30,
  "active_grace_seconds": 1200,
  "single_player_enabled": true,
  "offer_timeout_seconds": 900,
  "queue_max": 20,
  "local_max_hops": 3,
  "max_local_players": 2,
  "active_idle_seconds": 3600,
  "daemons_enabled": true,
  "daemon_idle_pause_seconds": 300,
  "daemon_min_interval_seconds": 60,
  "world_events_enabled": true,
  "world_events_channel_enabled": false,
  "world_events_channel_name": "",
  "broadcast_min_interval_seconds": 30,
  "inter_chunk_delay_ms": 800
}
```

Half-duplex radios cannot RX and TX at once. After each player command the plugin waits **`reply_settle_ms`** (default 2s) before sending the reply so the phone can finish retries. Identical normalized commands from the same sender within **`command_dedupe_seconds`** (default 20s) are dropped so retry storms do not re-run the game. On connect the companion enables MeshCore **`multi_acks=1`** (double ACK on receive) for more reliable player DM delivery confirmation.

## Build and inspect the wheel

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install build
rm -rf build dist *.egg-info
python -m build --wheel
python -m zipfile -l dist/zorcore_plugin-*-py3-none-any.whl
```

Checklist (OpenHop contract):

1. Exactly one manifest at `…/share/openhop/plugins/zorcore/openhop-plugin.json`
2. Package / manifest / `__version__` versions match
3. Tag `py3-none-any`
4. `.dist-info/entry_points.txt` contains console script `zorcore =`
5. `config.default.json` and `ui/{index.html,app.js,styles.css}` present
6. Content JSON under `zorcore/content/` (`meta.json`, `world.json`, `verbs.json`, `scripts.json`)
7. No `lobby.py`, `zorcore_zork`, `.venv`, credentials, `__pycache__`, or local logs

### Standalone smoke (no Repeater)

```bash
pip install dist/zorcore_plugin-*-py3-none-any.whl
export OPENHOP_PLUGIN_ID=zorcore
export OPENHOP_PLUGIN_DATA="$(mktemp -d /tmp/zorcore.XXXXXX)"
zorcore   # Ctrl-C to verify SIGTERM shutdown
```

## Catalogue submission (later)

Publish an **immutable** GitHub Release wheel on the plugin repository, then open a catalogue PR. Do not replace Release asset bytes after approval.

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

## License

Plugin code: MIT — see [`LICENSE`](LICENSE). Historical 1977 MDL sources are external ([MITDDC/zork](https://github.com/MITDDC/zork), MIT-0). Adventure prose under `zorcore/content/` follows the original MDL wording and is delivered over radio via multipart messages.
