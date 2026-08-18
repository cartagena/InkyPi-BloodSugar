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

There's a small stdlib-only `unittest` suite in `tests/` — no pytest, no other dependency, so it runs from a bare checkout of this repo alone:

```bash
python -m unittest discover -s tests -v
```

`tests/test_dexcom_client.py` covers the pure/deterministic parts of `dexcom_client.py` (trend normalization, WT timestamp parsing, mg/dL→mmol/L conversion) plus `DexcomClient`'s login and retry-once-after-session-expiry flow via a mocked `requests.post` — no network, no real InkyPi needed. `tests/test_blood_sugar.py` covers `BloodSugar._format_delta` and `_compute_value_color`; since `blood_sugar.py` imports InkyPi's `BasePlugin`, this file stubs that import with a minimal fake class so it can still run standalone.

This suite intentionally doesn't (and can't, without a real InkyPi checkout) exercise `generate_image`'s Jinja/Chromium rendering path or the live Dexcom API. For that:

1. Symlink `blood_sugar/` into a local InkyPi checkout: `ln -s <this-repo>/blood_sugar <inkypi-checkout>/src/plugins/blood_sugar`.
2. Render directly, bypassing Flask entirely — import InkyPi's `plugins.plugin_registry`, call `load_plugins([{"id": "blood_sugar", "class": "BloodSugar"}])`, get the instance, and call `plugin.render_image(dimensions, "blood_sugar.html", "blood_sugar.css", template_params)` with hand-built `template_params` (or `plugin.generate_image(settings, mock_device_config)` to exercise the real Dexcom API call). Save the returned `PIL.Image` and inspect it.
3. **Do not start InkyPi's Flask dev server** (`python src/inkypi.py --dev`) to test this plugin — prefer the direct render calls above. Keeps iteration fast and avoids needing a running server (or Chromium reachable from it) for every check.

`py_compile` on `blood_sugar.py`/`dexcom_client.py` catches syntax errors without needing InkyPi at all.

## Architecture

- **`blood_sugar/blood_sugar.py`** — the `BloodSugar(BasePlugin)` class InkyPi loads. `generate_image(settings, device_config)` is the entry point InkyPi calls on every refresh; must return a `PIL.Image` or raise `RuntimeError` with a user-facing message (InkyPi surfaces that message in the web UI). `generate_settings_template()` adds the "Requires API Key" tooltip and enables InkyPi's shared style-settings section (frame/margin/background/text color).
- **`blood_sugar/dexcom_client.py`** — the Dexcom Share API client, deliberately free of any InkyPi imports so it's portable/testable on its own. Two-step login (`AuthenticatePublisherAccount` → `LoginPublisherAccountById`), session-expiry retry-once in `fetch_latest`, and mg/dL/trend/timestamp parsing. `APPLICATION_ID`/`AGENT` are fixed values the unofficial API expects — don't change them.
- **`blood_sugar/render/`** — Jinja2 template (`blood_sugar.html`, extends InkyPi's `plugin.html`) + CSS, screenshotted by InkyPi's headless-Chromium pipeline (`BasePlugin.render_image`) into the final image. `render/icons/*.svg` are seven separately pre-authored trend-arrow icons (one per direction, plus two side-by-side variants for the rapid rise/fall states) — each pulled in via `{% include %}` based on `TREND_ICON_FILE[trend_name]` in `blood_sugar.py`. They are **not** one shape rotated at render time: an earlier version tried that and the visual bounding box (and thus the gap to the glucose value) shifted depending on rotation angle, since a non-square shape's axis-aligned footprint changes with rotation. Every file shares the same viewBox/centering math so all directions occupy an identical box.
- **`blood_sugar/settings.html`** — per-playlist-instance config (server region, display units, low/high glucose thresholds + colors). Rendered inside InkyPi's shared settings page; `name=` attributes become keys in the `settings` dict passed to `generate_image`. Follows InkyPi's `loadPluginSettings`/`pluginSettings` JS convention for prepopulating the form when editing an existing instance.

### Things worth knowing before changing behavior

- **InkyPi instantiates each plugin class once** at startup and reuses that instance across every playlist entry and refresh (`plugin_registry.load_plugins`). `BloodSugar.__init__` caches `DexcomClient`s in `self._clients` keyed by `(server, username)` rather than a single client on `self`, so multiple configured instances (e.g. different accounts) don't clobber each other's login session.
- **mg/dL is the source of truth.** `Reading.mg_dl` is what the API returns; mmol/L is derived for display via `mg_dl_to_mmol_l` (floor-based, matching the reference implementation this was ported from). Low/high threshold settings are always in mg/dL regardless of the selected display unit — comparisons happen directly against `latest.mg_dl`, no conversion.
- **Trend delta** is computed from the two most recent readings' `mg_dl` values in `blood_sugar.py`, not inside the client. The mmol/L delta uses a plain `round()`, deliberately not the same floor-based helper used for absolute values (floor biases negative deltas incorrectly).
- Credentials (`DEXCOM_USERNAME`/`DEXCOM_PASSWORD`) are read via `device_config.load_env_key(...)` from InkyPi's `.env`, never stored in per-instance `settings` — consistent with how InkyPi's other plugins (Weather, GitHub) keep secrets out of playlist config.
