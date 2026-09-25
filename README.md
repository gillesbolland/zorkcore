```text
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   ███████╗ ██████╗ ██████╗  ██████╗ ██████╗ ██████╗ ███████╗ ║
║   ╚══███╔╝██╔═══██╗██╔══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝ ║
║     ███╔╝ ██║   ██║██████╔╝██║     ██║   ██║██████╔╝█████╗   ║
║    ███╔╝  ██║   ██║██╔══██╗██║     ██║   ██║██╔══██╗██╔══╝   ║
║   ███████╗╚██████╔╝██║  ██║╚██████╗╚██████╔╝██║  ██║███████╗ ║
║   ╚══════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝ ║
║                                                              ║
║          MeshCore · OpenHop · Dungeon on the radio           ║
╚══════════════════════════════════════════════════════════════╝
```

# ZorCore

OpenHop Repeater plugin that brings the classic **Zork / Dungeon** text adventure to **MeshCore** — played over direct messages to the **Zork🕹️** companion, with original prose split for radio, a **shared live dungeon**, and a queue that still gives **local / low-hop players a fast pass**.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Plugin id: `zorcore` · distribution: `zorcore-plugin` · console script: `zorcore`

[Quick start](#quick-start) ·
[Features](#features-at-a-glance) ·
[Commands](#commands) ·
[Install](#install) ·
[Troubleshooting](#troubleshooting) ·
[How it works](#how-it-works) ·
[Reuse / fork](#reuse--fork)

[Project site](https://gillesbolland.github.io/zorkcore/) — how to play, plus an ASCII landing with a sample MeshCore DM thread.

## Quick start

> [!TIP]
> **Wheel in, companion up, share the join link.** On the Repeater: **Plugins** → upload/install the ZorCore `.whl` → **Enable**. Open `/plugins/zorcore/`, set up the companion, then share the `meshcore://` URL or QR with players. How to play (advert, join, `new game`): [project site](https://gillesbolland.github.io/zorkcore/).

## Features at a glance

| | |
| --- | --- |
| **Classic dungeon** | Full Zork / *Dungeon* map and puzzles as JSON — not a Z-machine |
| **Original prose** | LOOK / examine / readables kept; long text split across MeshCore messages |
| **Multipart clarity** | Story parts marked `1/3…` / `.3/3`; Exits tip sent separately |
| **Companion DMs** | Play by DM to **Zork🕹️**; public channels stay quiet by default (operator can opt in to short world-event lines) |
| **Shared world** | One living dungeon: same thief, troll, cyclops for concurrent locals |
| **Queue + fast-pass** | FIFO for remote players; path ≤ `local_max_hops` gets busy-local access |
| **Radio-friendly verbs** | `take all`, EXCEPT, AND lists, `it`/`them`, `look under` |
| **Resend vs redo** | `?` / `hello?` resends the last reply; `again` / `g` redos the last command |

## The original game

Zork (also known as *Dungeon*) is the MIT interactive-fiction landmark created by **Tim Anderson, Marc Blank, Bruce Daniels, and Dave Lebling**. Players type English commands, explore a huge underground empire, collect treasures, and match wits with a thief, a troll, a cyclops, and a map full of puzzles.

ZorCore ships that **full dungeon** (rooms, objects, treasures, exits) as JSON under [`zorcore/content/`](zorcore/content/). It is **not** a Z-machine and does not run Infocom story files. Vehicle puzzles from the MDL sources (bucket, balloon, mine cage, cake) are expressed as content scripts so every room stays reachable.

## Adapted for MeshCore radio

On a phone mesh you cannot dump a screenful of text in one shot, and you should not spam a public channel with game traffic. Gameplay stays in DMs; an operator can optionally mirror short world-event lines (a player entering, deaths, kills) to a named channel, which is off by default.

- Players **DM** the companion (**Zork🕹️**). Setup and QR live under `/plugins/zorcore/` on the Repeater.
- The **wording of the original LOOK / examine / readable text is kept**. Long replies are **not rewritten shorter for airtime**; they are **split into successive MeshCore messages** (`max_chunk_bytes`, up to `max_chunks`).
- Story parts carry trailing marks so you know when it is safe to type again:
  - `1/3…` — more story parts are still coming; wait
  - `.3/3` — the story reply is complete
- **Exits** (and other status tips) are a **separate unlabeled** message, listing only directions that are open *right now* — they are not part of the `n/m` count.
- Timed hazards use **⏱️** in the reply (act soon). There is no pause/resume.

## One shared dungeon

Everyone who is allowed to play shares **one** living world (not a private copy per player):

- The thief, troll, and cyclops have a single position, life, and (for the thief) stash in plugin data.
- Nearby friends can explore at the same time and meet the same NPCs; a kill can broadcast a short world-event line to other active players. Operators can also opt in to mirroring these events (a player entering, deaths, kills) to a named MeshCore channel — off by default; see [`PLUGIN_README.md`](PLUGIN_README.md).
- When nobody is playing, dungeon daemons pause so an empty cave does not “run ahead.”

## Queue and local fast-pass

Remote play follows **OpenHop Adaptive Rate Limiting** (advert tiers), not a wall clock. The Repeater must have ARL enabled.

| Who | How admission works |
| --- | --- |
| Remote / deeper hops | When the mesh is quiet enough: one active player if single-player mode is on; others wait in a **FIFO queue** and get a DM when their turn opens. |
| **Local fast-pass** | Companion path ≤ `local_max_hops` (default **3**) — still allowed under the busy-local exception when the mesh is too busy for remote play; up to `max_local_players` concurrent locals share the dungeon. |

Bans and operator promote/drop tools remain available in the plugin webadmin.

## Commands

### Start and help

| Command | What it does |
| --- | --- |
| `new game` / `new` / `start` / `play` | Begin (or restart) a run |
| `hello` / `?` (first contact) | Short how-to-start help |
| `help` | Short help |

### Movement and looking

| Command | What it does |
| --- | --- |
| `n` `s` `e` `w` `u` `d` (and diagonals) | Move |
| `look` / `l` | Full room description (multipart if needed) |
| `examine` / `x` / `look at …` | Describe an object |
| `look under …` | Puzzle-critical (e.g. rug, leaves); behind/through stub |
| `inventory` / `i` | What you carry |
| `score` | Score and moves |
| `wait` / `z` | Time passes |

### Radio-specific (this MeshCore build)

| Command | What it does |
| --- | --- |
| `?` / `hello?` / `hello` | **Resend the last reply** — use when a multipart answer sounded incomplete or a chunk was lost |
| `again` / `g` / `repeat` | **Redo your last gameplay command** (not a resend) |

### Inventory batching (saves round-trips)

| Command | Example |
| --- | --- |
| Take / drop / put all | `take all`, `drop all` |
| Except / but | `take all except lamp` |
| Valuables | `take valuables` |
| Comma / and lists | `take lamp, sword and paper` |
| Pronouns | `examine it`, `drop them` |

### Meta

| Command | What it does |
| --- | --- |
| `save` | Explicit checkpoint → `Saved.` |
| `restore` | Load checkpoint (or `No saved game.`) |
| `quit` / `q` / `bye` | Leave the play slot (checkpoint kept) |
| `brief` / `verbose` | Description verbosity (verbose by default; `brief` shortens revisits) |
| `version` | Plugin / content versions |

Most classic dungeon verbs are understood; unimplemented ones get a short stub reply instead of “I don't understand.”

## Install

Operator how-to — install ZorCore on an OpenHop Repeater and hand players a join link. For advert → join → `new game`, see the [project site](https://gillesbolland.github.io/zorkcore/).

1. Download the Release `.whl` (or build from this repo with `python -m build --wheel`).
2. On the Repeater **Plugins** page: upload the wheel, then **Enable**.
3. Open `/plugins/zorcore/` while logged into the dashboard.
4. Set up the companion (silent on `127.0.0.1:1977`), then copy the `meshcore://` URL or QR and share it privately with players.

### Requirements

| Item | Detail |
| --- | --- |
| Python | ≥ 3.10 |
| Host | [OpenHop Repeater](https://docs.openhop.dev/) with Adaptive Rate Limiting enabled |
| Dependency | `meshcore>=2.3.8` |

Operator settings, airtime, ARL, and daemon knobs: [`PLUGIN_README.md`](PLUGIN_README.md).

## Troubleshooting

| Symptom | What to try |
| --- | --- |
| No reply after a command | Wait for `.n/n` on multipart story; check companion path and that you are not only queued |
| Reply sounds incomplete | DM `?` or `hello?` to **resend** the last reaction (does not redo the move) |
| Want to repeat an action | `again` / `g` — redos the last gameplay command |
| Remote play always closed | Confirm ARL is enabled on the Repeater; locals (≤ hop limit) may still get a fast-pass |
| “Someone is already playing” | You are in the FIFO queue — wait for the turn offer DM, or play from a local hop path |

## How it works

- The plugin process talks to MeshCore through a **companion** on the Repeater (TCP). Player DMs are commands; replies are DM multiparts.
- Each command runs through a small parser and `Game.step` against baked content under `zorcore/content/`.
- Sessions and checkpoints are per-player on disk; **NPC / dungeon state is shared** (`npcs.json`) so concurrent locals meet the same world.
- Admission (bans, ARL gate, queue, local hop fast-pass) sits in front of play so radio airtime stays under operator control.

## Disclaimer (AI-assisted development)

This project was developed with **agentic / AI-assisted coding**. Review the code and packaging yourself before installing on a production Repeater.

## Reuse / fork

Feel free to reuse this project to adapt **other vintage Infocom-style text adventures** for MeshCore — same radio chunking, companion DMs, and shared-world patterns. Forks and ports of other classic interactive-fiction titles are welcome.

The [MIT](LICENSE) license covers the **plugin code**. Story rights for third-party games stay with their owners; only ship text and assets you have the right to use.

## License

Plugin code: [MIT](LICENSE).

## Credits

- Classic *Zork* / *Dungeon* by Tim Anderson, Marc Blank, Bruce Daniels, and Dave Lebling (MIT).
- Historical MDL sources (not vendored here): [MITDDC/zork](https://github.com/MITDDC/zork).
