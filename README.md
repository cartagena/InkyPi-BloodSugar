# InkyPi-BloodSugar

*InkyPi-BloodSugar* is a plugin for [InkyPi](https://github.com/fatihak/InkyPi) that displays your live Dexcom blood glucose reading, trend arrow, delta from the previous reading, and time of the last reading on an e-ink display.

## Screenshot

![InkyPi-BloodSugar screenshot](screenshot.png)

## Installation

Install the plugin using the InkyPi CLI, providing the plugin ID and this repository's URL:

```bash
inkypi plugin install blood_sugar https://github.com/cartagena/InkyPi-BloodSugar
```

## External API

This plugin depends on Dexcom's **Share API** — the same private, unofficial API the Dexcom Follow mobile apps use to poll a shared CGM account. There is no public developer program or official documentation for it (it's reverse-engineered), and it may change or break without notice. [pydexcom](https://github.com/gagebenne/pydexcom) is a well-known open-source client for this same API and is the closest thing to reference documentation available.

**Why not the official Dexcom API?** Dexcom does publish an [official developer API](https://developer.dexcom.com/), but it's explicitly not real-time: per its [data availability docs](https://developer.dexcom.com/docs/dexcomv3/endpoint-overview#data-availability), readings are delayed by **1 hour in the US and 3 hours outside the US**. That's unusable for a display meant to show your current glucose. The unofficial Share API is what the Dexcom Follow mobile app itself uses, so it reflects readings as soon as they sync — this plugin uses the same approach for the same reason. It's not a novel workaround either: [MMM-SugarValue](https://github.com/balharrie/MMM-SugarValue), a MagicMirror module with the same goal, uses the identical Share API for the identical reason.

- **Requires credentials, not a traditional API key.** You need your Dexcom account's username and password.
- **Requires Share to be enabled** on the Dexcom account, with at least one Follower configured (Settings → Share in the Dexcom G6/G7 app). The plugin authenticates as a follower, not the primary account, so add yourself as a follower if you haven't already.
- **Free**, with no formal request quota or paid tier — it's the same traffic pattern as the Dexcom Follow app checking in on a shared account. There's no cost beyond the active Dexcom CGM subscription you'd already need to be generating readings in the first place. Since it's an unofficial API, keep the refresh interval reasonable (every 5 minutes matches typical CGM update cadence) rather than polling aggressively.

### Setup

On the InkyPi web UI, go to the **API Keys** page and add:

```
DEXCOM_USERNAME=your-dexcom-username
DEXCOM_PASSWORD=your-dexcom-password
```

Then add the plugin to a playlist and choose your server region (US or Outside US), preferred units (mg/dL or mmol/L), and low/high glucose thresholds in the plugin settings.

## Development status

Actively maintained.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
