# Changelog

## 0.9.6

- Companion contact sync no longer gets stuck when `add_contact` times out with `no_event_received` (GitHub #1). All companion radio commands (`add_contact`, `remove_contact`, `get_contacts`) are now serialized with DM sends so their OK/ERROR events can't be stolen on a busy mesh.
- Per-key add cooldown plus a short circuit breaker: after repeated radio timeouts the sync loop pauses (`RADIO_PAUSED` in runtime stats) instead of hammering an unresponsive companion every 20s; a successful add/remove/DM or a reconnect clears it.
- Contact imports are capped per sync tick so one cycle can't monopolize the single companion radio.

## 0.9.5

- Verbose room descriptions by default (full text on every move); `brief` still available.
- Channel world-events also announce deaths (`🪦Name has been gobbled by a lurking grue in the attic!` / generic died line); nicknames only.

## 0.9.4

- Channel enter announce uses the player's last Chat Node advert nickname (or companion contact name), never a pubkey stub — public channels stay nickname-only.

## 0.9.3

- New game no longer doubles the start-room description (was forcing a near-duplicate `1/2…` / `.2/2` split).
- Multipart formatter uses a single unlabeled part when the text fits; label byte reserve only applies when a real split is needed.

## 0.9.2

- Active idle timeout (`active_idle_seconds`, default 1 h) frees local and remote slots without wiping sessions.
- Sticky locals: once seen on a short path, a player stays local through flood/unknown path flaps; webadmin Forget button.
- `local_max_hops` and `active_idle_seconds` in webadmin; `max_local_players` raised to **1–16**.
- Channel enter announce: `@Name has entered the dungeon.` when world-events channel is on (does not require DM fan-out).
- Advert→companion contact sync is **import-only** (no prune against the freshest-N advert window); junk cleanup still removes self/excluded. Surfaces `unresolved_senders` in runtime/webadmin.
- Companion `region_scope` clear uses `""` / no-arg (avoids TypeError on connect).

## 0.9.1

- Active-player grace through busy mesh flaps (`active_grace_seconds`).
- Optional world-event publish to a MeshCore autochannel by name.
- Region picker from OpenHop transport keys; companion never auto-adverts.
- Webadmin polish; ban toggle labeled **Enforce ban list**.

## 0.9.0

- Initial ZorCore OpenHop plugin release.
