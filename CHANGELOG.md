# Changelog

## 0.2.1

- Propagate invalid authentication to Home Assistant reauthentication.
- Reject cloud HTTP redirects; avoid exposing signed URLs in request exceptions.
- Convert Tuya decimal scale and measurement units separately for electrical sensors.
- Correct LAN command regression tests for `set_multiple_values` and cover rejection/timeouts.
- Preserve areas manually assigned or cleared in Home Assistant across reloads.
- Bound concurrent cloud/device polling and back off on individual DPS fetch failures.
- Keep cloud discovery on schedule during LAN updates and register capabilities when schemas arrive.
- Isolate invalid device metadata and mark missing device entities unavailable until they return.
- Stop the LAN monitor on unload, including when a refresh is in flight.
- Handle HTTP authentication and rate-limit responses and keep signed URLs out of transport logs.
- Remove Local Key sensors and provide Recorder cleanup guidance for affected upgrades.
- Add regression tests, release checks and contributor attribution.

## 0.2.0

- Upstream baseline and bug fixes as described in the project's existing history.
