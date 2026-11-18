[![Current Release](https://img.shields.io/github/release/srijken/webastoconnect/all.svg?style=plastic)](https://github.com/srijken/webastoconnect/releases) [![Github All Releases](https://img.shields.io/github/downloads/srijken/webastoconnect/total.svg?style=plastic)](https://github.com/srijken/webastoconnect/releases) [![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=plastic)](https://github.com/hacs/integration)

Originally created by [MTrab](https://github.com/MTrab/webastoconnect), now maintained by [srijken](https://github.com/srijken).

This integration provides support for Webasto ThermoConnect devices.

# Please note
Webasto <b>DOES NOT</b> provide any public API or documentation for it, so I cannot provide any guarantees that this will continue to work long-term.

# Table of Contents

**[Installation](#installation)**<br/>
**[Setup](#setup)**<br/>
**[Known Issues](#known-issues)**<br/>

# Installation:

### Option 1 (easy) - HACS:

- Ensure that HACS is installed.
- Add `https://github.com/srijken/webastoconnect` as a custom repository (type: Integration).
- Search for and install the "Webasto Connect" integration.
- Restart Home Assistant.

### Option 2 - Manual installation:

- Download the latest release.
- Unpack the release and copy the `custom_components/webastoconnect` directory into the `custom_components` directory of your Home Assistant installation.
- Restart Home Assistant.

# Setup

Open setup in Home Assistant:<br/>
[![](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=webastoconnect)

Or go to Home Assistant > Settings > Integrations

Add "Webasto Connect (ThermoConnect)" integration *(If it doesn't appear, try a hard refresh with Ctrl+F5.)*

Enter your Webasto account email and password

To change the password later, open the integration and choose **Reconfigure**.

## Upgrading: stable entity unique IDs

Entity unique IDs no longer contain the entity name, so renaming an output in
the Webasto app, or switching between heating and ventilation, no longer
creates a new entity. On the first start after upgrading, existing entities
are moved to the new unique IDs once; entity IDs, history and customisations
are kept. Entities that cannot be matched (for example an orphaned entity left
behind by an earlier output rename) are left unchanged and can be removed by
hand. Make a Home Assistant backup before upgrading: going back to an older
version afterwards creates duplicate entities (with a `_2` suffix).

## Entity names and languages

Entity names follow the Home Assistant language (Czech, Danish, Dutch,
English, Finnish, French, German, Norwegian Bokmål and Spanish). The names of
the outputs (main output, AUX1, AUX2) come from the Webasto app and are not
translated. Existing entity IDs do not change. A new install uses the
language of Home Assistant for new entity IDs, as Home Assistant does for
other integrations; with English the entity IDs are the same as before.

## Webasto Connect Card

The integration includes a dashboard card. It loads automatically on every
dashboard once the integration is set up: no Lovelace resource and no file in
`/config/www` are needed. Add it from the card picker ("Webasto Connect Card")
or with YAML:

```yaml
type: custom:webasto-connect-card
device_id: <your_home_assistant_device_id>
```

See `card-src/webasto_connect_card.yaml` for all options.

Upgrading from 1.4.1 or older: those versions copied the card to
`/config/www/webastoconnect/` and added the Lovelace resource
`/local/webastoconnect/webasto-connect-card.js`. On the first start after
upgrading the integration removes that resource (UI-managed resources only)
and the copied file, and logs what it removed. Existing cards keep working.
If your resources are defined in YAML, remove that entry yourself; the log
shows a warning while it is still there. Reload the browser (or clear the
app cache) once after upgrading.

# Known Issues

## My heater doesn't show up

If your heater doesn't show up in the integration, please make sure it is connected to the email used.

* Login to https://my.webastoconnect.com _USING THE SAME EMAIL AND PASSWORD_ as used in the integration
* Press `Account`

Make sure your device is listed under devices

If your device is NOT listed under devices:

* Open the ThermoConnect app on your phone
* Select the missing device (If you have more than one connected)
* Click on the `My Webasto Connect` button in the lower left
* Choose `Login with mobile browser`
* Login with your existing email and password

The device should now be linked to your email account and will show up after a Home Assistant restart or after reloading the integration.

## Webasto Connect Card map popup centering

When opening the `Map` popup in the custom `Webasto Connect Card`, the tracked entity marker can appear visually lower than expected instead of perfectly centered.

This behavior comes from Home Assistant's built-in map rendering in popup or modal contexts. The integration currently uses the built-in map implementation and does not override this behavior.
