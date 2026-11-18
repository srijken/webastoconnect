# Webasto Connect Card

Custom Home Assistant Lovelace card.

## Files
- `webasto-connect-card.js`: source for the custom card
- `../custom_components/webastoconnect/card/webasto-connect-card.js`: built single-file card module (generated)
- `localize/localize.js`: translation lookup and language fallback
- `translations/*.json`: per-language strings
- `webasto_connect_card.yaml`: example card configuration

## Build (separate from release)
From repository root:

```bash
cd card-src
npm install
npm run build
```

For iterative testing while developing:

```bash
cd card-src
npm run build:watch
```

Card version marker (`__WEBASTO_CONNECT_CARD_VERSION__`) is injected automatically
from `card-src/package.json` during build.

## Install in Home Assistant
No install step: the integration serves the built card from its package at
`/webastoconnect/webasto-connect-card.js` and loads it on every dashboard
(`frontend.add_extra_js_url`), so no Lovelace resource is needed.

Use the example from `card-src/webasto_connect_card.yaml` and select your Webasto device ID in the card editor.
Manual entity IDs are still supported as overrides when needed.

## Language / translations
- The card auto-selects text from Home Assistant language (`hass.language`).
- Built-in translations currently include `da` and `en` (fallback: `en`).
- You can still override corner titles with:
  - `title_geo_fence`
  - `title_mode`
  - `title_timers`
  - `title_map`
