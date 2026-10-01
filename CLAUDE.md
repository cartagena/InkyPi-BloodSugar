# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

InkyPi-BloodSugar is a third-party plugin for [InkyPi](https://github.com/fatihak/InkyPi), an e-ink display app for Raspberry Pi. It polls the unofficial Dexcom Share API and renders current blood glucose, trend arrow, delta from the previous reading, and time of the last reading.

This repo is *not* a standalone application — it contains only the plugin, meant to be installed into an existing InkyPi instance. The entire repo's content is the single `blood_sugar/` folder (plus README/LICENSE); nothing here runs on its own.

## Installation (end-user)

```bash
inkypi plugin install blood_sugar https://github.com/cartagena/InkyPi-BloodSugar
```

This is InkyPi's plugin CLI: it does a `git sparse-checkout` of the folder named exactly after the plugin id and copies its contents into `<inkypi>/src/plugins/blood_sugar`. **The top-level `blood_sugar/` folder name must exactly match `plugin-info.json`'s `"id"` field** — this is a hard requirement of the install mechanism, not just a convention.

Credentials go in InkyPi's root `.env` (via its `/api-keys` web UI), not in plugin settings:
```
DEXCOM_USERNAME=your-dexcom-username
DEXCOM_PASSWORD=your-dexcom-password
```
(Env var names stay `DEXCOM_*` and internal identifiers like `DexcomClient`/`dexcom_client.py` stay as-is — they accurately describe the Dexcom Share API this plugin talks to. Only the project/plugin's own identity — repo name, plugin id/folder, display name, main class — dropped "Dexcom" to avoid using the trademark in the project's own name.)

The Dexcom account needs Share enabled with at least one Follower configured — the plugin authenticates as a follower, not the primary account.

## Verifying changes

Two suites, split by what they require. Both run from this repo — nothing needs copying into an InkyPi checkout.

```bash
pip install -r requirements-dev.txt
pytest tests/unit                    # anywhere: no InkyPi, no network, no browser
INKYPI_PATH=../InkyPi pytest         # everything, against a real InkyPi checkout
```

The test bodies are still `unittest.TestCase` (pytest collects them natively); the runner changed, the style didn't. `tests/conftest.py` registers a stand-in for the one host module `blood_sugar.py` imports unguarded (`BasePlugin`) **only when a real InkyPi isn't importable** — when `INKYPI_PATH` is set it puts `<inkypi>/src` on `sys.path` and stubs nothing, so the identical unit tests run against real host code. CI runs the unit suite both ways deliberately; that's what stops the stub from drifting away from InkyPi's actual behaviour. Note the plugin's *other* host import (the settings-schema DSL) needs no stub: it's already guarded by a try/except so the file stays importable on upstream InkyPi, and standalone that guard simply takes its ImportError branch.

Two consequences worth keeping in mind:

- **The stub must stay minimal.** Its `render_image` *raises* rather than returning a placeholder image, so a template regression cannot pass the unit suite — covering that is the integration suite's job.
- **If `INKYPI_PATH` is set but the host still won't import, the conftest raises** rather than falling back to stubs. Silently stubbing there would let CI's integration job report green while having quietly run the unit suite twice.

`tests/integration/` needs `blood_sugar/` symlinked into the checkout (see below) and covers what only real host code can show: loading through the real plugin registry, `plugin-info.json` agreeing with the registered class, all seven trend-arrow SVGs actually rendering (they're `{% include %}`d, so a missing icon only fails at render time), the stale treatment reaching real pixels, and API/credential failures surfacing as `RuntimeError` so InkyPi shows them in the web UI. **Dexcom is mocked in every one of them** — the suite must never touch a real CGM account.

`tests/unit/test_dexcom_client.py` covers the pure/deterministic parts of `dexcom_client.py` (trend normalization, WT timestamp parsing including its UTC-awareness, mg/dL→mmol/L conversion, malformed-response handling) plus `DexcomClient`'s login and retry-once-after-session-expiry flow via a mocked `requests.post`. `tests/unit/test_blood_sugar.py` covers `_format_delta`, `_compute_value_color`, the client cache's keying, and the staleness/timezone display logic (`_reading_age`, `_format_age`, `_format_reading_time`). Both import the plugin under its real dotted path (`plugins.blood_sugar.*`) rather than as a bare top-level module — that's what lets the same files run unmodified against a real InkyPi checkout.

To set up the integration environment, or to poke at a render by hand:

1. Symlink `blood_sugar/` into a local InkyPi checkout: `ln -s <this-repo>/blood_sugar <inkypi-checkout>/src/plugins/blood_sugar`.
2. `INKYPI_PATH=<inkypi-checkout> pytest tests/integration` runs the real render path against a mocked Dexcom API. For one-off inspection, render directly and bypass Flask entirely — import InkyPi's `plugins.plugin_registry`, call `load_plugins([{"id": "blood_sugar", "class": "BloodSugar"}])`, get the instance, and call `plugin.render_image(dimensions, "blood_sugar.html", "blood_sugar.css", template_params)` with hand-built `template_params`. Save the returned `PIL.Image` and inspect it.
3. **Do not start InkyPi's Flask dev server** (`python src/inkypi.py --dev`) to test this plugin — prefer the integration suite or the direct render call above. Keeps iteration fast and avoids needing a running server (or Chromium reachable from it) for every check.

`py_compile` on `blood_sugar.py`/`dexcom_client.py` catches syntax errors without needing InkyPi at all.

## Architecture

- **`blood_sugar/blood_sugar.py`** — the `BloodSugar(BasePlugin)` class InkyPi loads. `generate_image(settings, device_config)` is the entry point InkyPi calls on every refresh; must return a `PIL.Image` or raise `RuntimeError` with a user-facing message (InkyPi surfaces that message in the web UI). `generate_settings_template()` adds the "Requires API Key" tooltip and enables InkyPi's shared style-settings section (frame/margin/background/text color).
- **`blood_sugar/dexcom_client.py`** — the Dexcom Share API client, deliberately free of any InkyPi imports so it's portable/testable on its own. Two-step login (`AuthenticatePublisherAccount` → `LoginPublisherAccountById`), session-expiry retry-once in `fetch_latest`, and mg/dL/trend/timestamp parsing. `APPLICATION_ID`/`AGENT` are fixed values the unofficial API expects — don't change them.
- **`blood_sugar/render/`** — Jinja2 template (`blood_sugar.html`, extends InkyPi's `plugin.html`) + CSS, screenshotted by InkyPi's headless-Chromium pipeline (`BasePlugin.render_image`) into the final image. The `.stale` modifier class on `.blood-sugar-container` drives the stale-reading treatment; it uses opacity rather than a hue shift because opacity survives the panel's 6-color quantization intact. `render/icons/*.svg` are seven separately pre-authored trend-arrow icons (one per direction, plus two side-by-side variants for the rapid rise/fall states) — each pulled in via `{% include %}` based on `TREND_ICON_FILE[trend_name]` in `blood_sugar.py`. They are **not** one shape rotated at render time: an earlier version tried that and the visual bounding box (and thus the gap to the glucose value) shifted depending on rotation angle, since a non-square shape's axis-aligned footprint changes with rotation. Every file shares the same viewBox/centering math so all directions occupy an identical box.
- **`blood_sugar/settings.html`** — per-playlist-instance config (server region, display units, low/high glucose thresholds + colors). Rendered inside InkyPi's shared settings page; `name=` attributes become keys in the `settings` dict passed to `generate_image`. Follows InkyPi's `loadPluginSettings`/`pluginSettings` JS convention for prepopulating the form when editing an existing instance.

### Things worth knowing before changing behavior

- **InkyPi instantiates each plugin class once** at startup and reuses that instance across every playlist entry and refresh (`plugin_registry.load_plugins`). `BloodSugar.__init__` caches `DexcomClient`s in `self._clients` keyed by `(server, username, password)` rather than a single client on `self`, so multiple configured instances (e.g. different accounts) don't clobber each other's login session. The password is part of the key deliberately: without it, a client cached under an old password would keep failing to log in until the service was restarted after a credential change.
- **Staleness is a display concern, and a deliberate one.** An e-ink panel keeps showing the last render indefinitely, so a reading whose data flow died an hour ago is visually indistinguishable from a live one — on a glucose display that's the failure mode that matters most. `generate_image` computes the reading's age and, past `STALE_AFTER_MINUTES` (15 — three missed CGM readings), sets `is_stale`, which dims the reading in CSS, adds an explicit `NO UPDATE · <age>` line, **and suppresses `value_color`** so a red "55" from an hour ago doesn't read as a live emergency. Don't "simplify" this away.
- **All timestamps are UTC internally.** `Reading.timestamp` is timezone-aware UTC (`_parse_reading` passes `UTC` to `fromtimestamp`); `_format_reading_time` converts to the *device's configured* timezone (`device_config.get_config("timezone")`) via stdlib `zoneinfo`, falling back to UTC on an unknown zone. Naive local time would silently shift every reading whenever the Pi's system clock zone and the display's configured timezone disagree — the normal case on a headless Pi left at UTC. `zoneinfo` rather than the fork's `utils.time_utils` helpers, to keep this file importable on upstream InkyPi.
- **mg/dL is the source of truth.** `Reading.mg_dl` is what the API returns; mmol/L is derived for display via `mg_dl_to_mmol_l` (floor-based, matching the reference implementation this was ported from). Low/high threshold settings are always in mg/dL regardless of the selected display unit — comparisons happen directly against `latest.mg_dl`, no conversion. Non-numeric thresholds fall back to the defaults rather than raising: a Layout region's settings can be hand-edited as raw JSON, where the settings form's `type="number"` input isn't there to constrain them.
- **Trend delta** is computed from the two most recent readings' `mg_dl` values in `blood_sugar.py`, not inside the client. The mmol/L delta uses a plain `round()`, deliberately not the same floor-based helper used for absolute values (floor biases negative deltas incorrectly).
- Credentials (`DEXCOM_USERNAME`/`DEXCOM_PASSWORD`) are read via `device_config.load_env_key(...)` from InkyPi's `.env`, never stored in per-instance `settings` — consistent with how InkyPi's other plugins (Weather, GitHub) keep secrets out of playlist config.
