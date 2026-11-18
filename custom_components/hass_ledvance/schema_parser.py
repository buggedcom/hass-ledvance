"""Parse Tuya device schema to detect entity type and DPS number mappings."""

from __future__ import annotations

import json
from contextlib import suppress

from .const import KNOWN_PRODUCT_TYPES

# DPS codes that indicate a light device
_LIGHT_CODES = frozenset(
    {
        "bright_value",
        "bright_value_v2",
        "colour_data",
        "colour_data_v2",
        "temp_value",
        "work_mode",
        "mix_rgbcw",
    }
)


def normalize_schema(schema: object) -> list[dict]:
    """Keep usable data points from an optional device schema."""
    if schema is None:
        return []
    if not isinstance(schema, list):
        raise ValueError("Unexpected device schema")
    return [
        item for item in schema
        if isinstance(item, dict) and isinstance(item.get("code"), str) and item.get("id") is not None
    ]


def detect_device_type(schema: list[dict], product_id: str) -> str:
    """Return 'light', 'socket_strip', 'switch', or 'unknown' for a device.

    Detection order:
    1. Schema-based: presence of known light/socket-strip/switch DPS codes
    2. Product ID table fallback
    """
    codes = {item.get("code", "") for item in schema}

    # Light detection: any light-specific DPS present
    if codes & _LIGHT_CODES:
        return "light"

    # Socket strip: requires multiple numbered outlets (switch_1, switch_2, …)
    # A single smart plug with power monitoring DPS is still just a "switch".
    numbered_switches = [c for c in codes if c.startswith("switch_") and c[7:].isdecimal()]
    if len(numbered_switches) > 1:
        return "socket_strip"

    # Single switch device
    if any(c.startswith("switch") for c in codes):
        return "switch"

    # Product ID fallback
    return KNOWN_PRODUCT_TYPES.get(product_id, "unknown")


def get_dps_map(schema: list[dict]) -> dict[str, str]:
    """Return a mapping of DPS code name → DPS number string.

    E.g. {"switch_led": "1", "bright_value": "3", "temp_value": "4"}

    The schema item format from the Tuya API is:
      {"id": 1, "code": "switch_led", "type": "Boolean", "mode": "rw", ...}
    """
    result: dict[str, str] = {}
    for item in schema:
        code = item.get("code")
        dps_id = item.get("id")
        if code and dps_id is not None:
            result[code] = str(dps_id)
    return result


def get_schema_property(schema: list[dict], code: str) -> dict | None:
    """Return the schema entry for a specific DPS code, or None."""
    for item in schema:
        if item.get("code") == code:
            return item
    return None


def _get_properties(schema: list[dict], code: str) -> dict:
    """Return the data point properties whether supplied as an object or JSON."""
    prop = (get_schema_property(schema, code) or {}).get("property")
    if isinstance(prop, str):
        with suppress(ValueError):
            prop = json.loads(prop)
    return prop if isinstance(prop, dict) else {}


def get_integer_range(schema: list[dict], code: str) -> tuple[int, int]:
    """Return (min, max) for an Integer-type DPS, or (0, 1000) as default."""
    prop = _get_properties(schema, code)
    try:
        lo, hi = int(prop.get("min", 0)), int(prop.get("max", 1000))
        return (lo, hi) if lo <= hi else (0, 1000)
    except (ValueError, TypeError, OverflowError):
        return (0, 1000)


def get_numeric_scale(schema: list[dict], code: str) -> float | None:
    """Return the decimal divisor for a raw Tuya value in its declared unit.

    Tuya encodes sensor values as integers where the 'scale' property gives
    the number of decimal places. A scale of 1 divides the raw value by 10.
    """
    try:
        places = int(_get_properties(schema, code).get("scale"))
        return 10.0 ** places if 0 <= places <= 12 else None
    except (ValueError, TypeError, OverflowError):
        return None


def get_numeric_unit(schema: list[dict], code: str) -> str:
    """Return the reported measurement unit if supplied."""
    unit = _get_properties(schema, code).get("unit")
    return unit.strip() if isinstance(unit, str) else ""


def get_enum_range(schema: list[dict], code: str) -> list[str]:
    """Return list of valid enum values for an Enum-type DPS, or []."""
    values = _get_properties(schema, code).get("range")
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str)]


def get_socket_outlet_dps(schema: list[dict]) -> list[tuple[str, str]]:
    """Return [(dps_code, label), ...] for every controllable outlet on a socket strip.

    Order: numbered outlets (switch_1, switch_2, …), then USB ports,
    then an "all outlets" master if present.
    """
    codes = {item.get("code", "") for item in schema}
    result: list[tuple[str, str]] = []

    # Numbered outlets: switch_1, switch_2, …
    numbered = sorted(
        (c for c in codes if c.startswith("switch_") and c[7:].isdecimal()),
        key=lambda c: int(c[7:]),
    )
    for code in numbered:
        result.append((code, f"Outlet {code[7:]}"))

    # USB ports
    if "usb_switch" in codes:
        result.append(("usb_switch", "USB"))
    for i in range(1, 5):
        usb_code = f"switch_usb{i}"
        if usb_code in codes:
            result.append((usb_code, f"USB {i}"))

    # Master / all-outlets switch
    for master_code in ("master_switch", "switch_all"):
        if master_code in codes:
            result.append((master_code, "All Outlets"))
            break

    return result


def has_hardware_master(schema: list[dict]) -> bool:
    """Return True if the schema contains a hardware master-switch DPS code."""
    codes = {item.get("code") for item in schema}
    return bool(codes & {"master_switch", "switch_all"})
