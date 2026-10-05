# MistCiscoConfigConverter agent instructions

This file holds rules for `MistCiscoConfigConverter` only. The rules for each
repository of this owner are in `AGENTS.md` at the repository root. Read
`AGENTS.md` first. This file adds to it, and it does not hold a copy of a rule
from it. Where the two files disagree, obey `AGENTS.md` for a writing rule, a
safety rule, or a security rule.

## What this repository is

MistCiscoConfigConverter is a Python web application for network engineers. It
converts Cisco IOS and IOS-XE configuration files to Juniper Mist settings. The
browser shows a proposal before the user applies it. The user guide explains
local Python use on Windows, macOS, and Linux. Podman and Docker run the
application in a container.

## Language and environment

The application uses Python 3.13, Flask, `mistapi` 0.64.0, Gunicorn, and
gevent. Copy `.env.example` to the ignored `.env` file. Set `MIST_APITOKEN`,
`MIST_HOST`, `org_id`, and `SECRET_KEY` there. Install the dependencies in a
virtual environment.

```sh
python -m venv .venv
python -m pip install -r requirements.txt
```

## Local gates

| Gate | Command | Expected result |
| - | - | - |
| Offline and documentation tests | `python -m unittest discover -s tests -v` | All tests pass |
| STE documents | `ste-linter --config .ste-linter.toml --min-score 80 README.md docs/*.md AGENTS.md .github/copilot-instructions.md` | Each file scores 80 or higher |
| Canonical instruction check | `agent-instructions-check --commit da02d4c6a2163d1882f2ad25fce80b8ba38304d1` | `AGENTS.md` matches the canonical file |

This repository defines no formatter, type checker, or Python lint command.

## Architecture and conventions

`parser/cisco_parser.py` parses Cisco configurations. `app.py` serves the Flask
interface and coordinates conversion. The classes in `mist/` call the Mist API.
`templates/index.html` holds the interface. `static/css/themes.css` holds its
themes.

The converter supports `branch`, `hub`, and `standalone` gateway roles. Branch
gateways use gateway templates with `type: "spoke"`. Standalone gateways use
gateway templates with `type: "standalone"`. Hub gateways use device profiles
with `type: "gateway"`. Hub profiles store NTP and DNS values directly. Branch
and standalone templates use site variables for those values. SRX is the
default hardware type. Hub profiles attach to devices. Branch and standalone
templates attach to sites. SSR does not use the DNS suffix setting.

WAN and LAN classification uses weighted indicators. The default score
thresholds are `0.3` for WAN and `-0.3` for LAN. The app includes WAN
interfaces with confidence of at least `0.7`. Set
`INTERFACE_WAN_THRESHOLD` or `INTERFACE_LAN_THRESHOLD` to change the score
thresholds. Tunnel interfaces keep the `tunnel` role and do not enter the WAN
list. For a hub, warn when a WAN interface uses DHCP. Recommend a static address.

Normal WAN slots sort by score. Cellular slots sort by interface name after
normal WAN slots. Each detected cellular interface creates a modem slot and an
LTE slot. WAN names start at `wan1`. LTE names add `_lte`. The code sets no
fixed limit on WAN slots.

The parser reads static, DHCP, PPPoE, and negotiated IP settings. It also reads
subnet masks, VLAN tags, shutdown state, default gateways, and cellular profile
data. A cellular profile can set an APN, authentication, username, password,
PDP type, and SIM slot. The parser decodes Type 7 passwords.

Bandwidth takes these sources in order: interface `bandwidth`, source tunnel
bandwidth, source tunnel description, then interface description. Description
speeds support `BDW=upload/download` and common K, M, and G values. The
`bandwidth_source` value records the selected source.

WAN site variables include the interface name and any available IP, subnet
prefix, gateway, VLAN, and IP configuration type. LTE variables use `_apn`,
`_auth`, `_user`, and `_pass` suffixes. Network variables use the Mist network
name with `_network`, `_prefix`, and `_vlan` suffixes. The template adds upload
traffic shaping variables. The site manager creates those variables only when
bandwidth values exist.

The parser and conversion tests use `unittest`. Keep tests in `tests/`.
Spec Kit writes generated agent context files under
`.specify/memory/agent-context-*.md`. Do not use these files as instruction
files. Keep the README sections `What`, `How`, `Where`, `When`, `Why`, and `Who`.

## Safety in this repository

The app reads Cisco files from `input/`, writes logs to `data/`, and stores
backups in `output/`. The route `/api/apply` updates settings in Mist. The
browser enables its apply button only after the user types `APPLY`. The route
rejects a request without the JSON value `"confirmation": "APPLY"`. It then
sends no request to Mist. The constant `APPLY_CONFIRMATION_WORD` in `app.py`
holds the word. Power user routes
require `POWERUSER=true`. Routes that restore, delete, or unassign objects
require the JSON value
`"confirmation": "CONFIRM"`. Backup files use timestamped names in `output/`.
Their prefixes are `service_policies_backup_`, `services_backup_`, and
`networks_backup_`.
Power user mode backs up and restores service policies, services, and networks.
It also deletes services and networks and can unassign or delete templates or
templated devices. Its API routes use the `/api/poweruser/` prefix.

## Containers and ports

The Compose service is `converter`. It uses the container name
`mist-cisco-converter` and publishes port `8000`. Mount `data/`, `input/`, and
`output/` as volumes. Use `podman-compose up -d` or `docker compose up -d` for
the local stack. The repository defines no test port range. Pass `--format docker` to a Podman
build so the image supports `HEALTHCHECK`. Use `:Z` on bind mounts for SELinux
support. Rebuild the image and restart the running container after a
configuration, Python, template, or static asset change.

## Git and GitHub in this repository

Use the `documentation` label for documentation changes. Use `in-progress` to
mark active work. The repository records its change history in
`docs/USER_GUIDE.md` under `Changelog`. The `test` workflow check is required
on `main`. The STE workflow checks the README, the instruction files, and the
three Markdown files in `docs/`. The other workflow reports stranded branches
each week. The repository is public, so its standard GitHub-hosted workflow
runs have no Actions-minute cost. It has no CodeQL workflow, `auto-merge` label,
or pull request template. Dated changelog headings use `vYY.MM.DD`.

## Key files

| File | Purpose |
| - | - |
| `app.py` | Flask routes and conversion flow |
| `parser/cisco_parser.py` | Cisco configuration parser |
| `mist/` | Mist API managers |
| `docs/USER_GUIDE.md` | Setup, operation, and change history |
| `docs/CISCO_TO_MIST_MAPPING.md` | Cisco and Mist configuration mappings |

## External resources

Use `docs/CISCO_TO_MIST_MAPPING.md` for conversion details. Use the [Mist OpenAPI
specification](https://github.com/mistsys/mist_openapi) for API details. Find
the pinned `mistapi` version in `requirements.txt`.
