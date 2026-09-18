# OpenHop catalogue submission notes

This file prepares a future [openHop plugin catalogue](https://github.com/openhop-dev/openhop-plugin-catalogue) PR.

**Blocked while the repository is private.** Catalogue entries need a public GitHub Release wheel URL and a public source repo for review. Flip [gillesbolland/zorkcore](https://github.com/gillesbolland/zorkcore) to public before opening a catalogue PR.

## Schema-2 fields (draft)

| Field | Value |
| --- | --- |
| `id` | `zorcore` |
| `name` | `ZorCore` |
| `distribution` | `zorcore-plugin` |
| `repository` | `https://github.com/gillesbolland/zorkcore` |
| `version` | `0.9.0` |
| `category` | verify against catalogue README at submission time (likely games / entertainment) |
| `logo` | TBD before catalogue PR (HTTPS image URL) |
| `source_revision` | `16d2782fbb0842febe5130e8178356540b1c6e05` |
| `wheel_url` | https://github.com/gillesbolland/zorkcore/releases/download/v0.9.0/zorcore_plugin-0.9.0-py3-none-any.whl |
| `sha256` | `eda4b5d2c5439276b13832d121d54b1da3759452d13f8d90d66dac437ff22eed` (private Release; re-verify after going public) |

## Checklist before catalogue PR

1. Repo is **public**
2. Immutable GitHub Release `v0.9.0` (or newer) with the wheel
3. Download the wheel; compute SHA-256 of those bytes
4. Document tested OpenHop Repeater version
5. Fork catalogue repo, add entry, run local validation, open PR for human review

Do not commit the wheel or secrets into the catalogue repository.
