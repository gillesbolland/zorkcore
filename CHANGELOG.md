# Changelog

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
