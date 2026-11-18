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

## Subscription expiry reminder

The ThermoConnect subscription does not renew automatically. From 30 days before the expiration date (the *Subscription expiration* sensor), a repair issue in Home Assistant (Settings > System > Repairs) reminds you to renew it on [My Webasto Connect](https://my.webastoconnect.com); once it has expired the issue becomes an error, because the service is suspended and a reactivation fee applies. The issue goes away by itself after the renewal shows up in the cloud data. The *Subscription days left* sensor shows the same count of days (0 on the last day, negative once expired), for use in your own automations or dashboards.

## Next event sensor

The *Next event* diagnostic sensor (disabled by default) shows the next timer
event as reported by the Webasto cloud itself. Its state is only a time of day
(HH:MM, in the Home Assistant timezone): the cloud also sends a `day` value,
but its meaning is not known yet, so no date is shown. The time is converted
from UTC with today's offset, so around a daylight saving time change it can
be off by an hour. Attributes: `line` (Heater or Ventilation), `type` (simple
or smart), `time_utc` and `day_raw` (the raw `day` value). Compare it with the
*Next start* sensor, which the integration calculates from the simple and
smart timers.

## Smart timers

Besides simple timers (a start time and a duration), the Webasto app can set
*smart* timers: you set a departure time and the heater starts by itself
before it, at most the maximum duration earlier. Smart timers are shown in
the card and in the `timers` attribute of the *Next start* sensor
(`type: smart`, `departure_hhmm_utc`, `max_duration` in seconds,
`comfort_level`, `repeat` and `has_location`). For a smart timer,
`next_run_utc` (and the sensor state, when it is the next timer) is the
departure time, not the moment the heater starts.

Actions:

- `webastoconnect.create_smart_timer`: `departure_time` (local HH:MM),
  `max_duration_minutes` (1 to 120), `comfort_level` (default 5), and
  optionally `line`, `repeat_days`, `enabled` and `location`.
- `webastoconnect.update_smart_timer`: the same fields, plus `timer_index`.
- `webastoconnect.delete_timer` with `type: smart`.

`timer_index` counts the timers of one type on one output: the index of a
simple timer does not change when smart timers are added, and existing
automations that use `update_timer` or `delete_timer` keep working. The
`index` of each entry in the `timers` attribute is this index; pass the
entry's line as `line` (and `type` for `delete_timer`).

Only comfort level 5 has been seen in the Webasto app, so other values are
accepted (1 to 10) but not verified. **Saving smart timers from Home
Assistant has not been verified against a real device yet**: check the timer
in the Webasto app after creating or changing one.

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

The *Timers* popup lists the timers of one output. On a device with a
ventilation mode, a Heater / Ventilation switch at the top chooses which
output's timers are shown (the active output when the popup opens), and the
timer form has an *Output* field for new timers. The output of an existing
timer is shown but cannot be changed: delete it and create a new one instead.

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
