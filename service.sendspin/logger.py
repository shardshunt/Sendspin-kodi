#!/usr/bin/env python3
"""
Centralised logging and diagnostic helpers for the Sendspin service.

- mode controls whether we attach log handlers for unhandled listeners,
  for all listeners, or none.
    - "unhandled" (default): attach only to listeners not explicitly handled by
      the service (safe default).
    - "all": attach to every add_*_listener method (except those explicitly
      excluded below).
    - "none": do not attach any automatic listeners.
"""

from __future__ import annotations

import logging
from typing import Any

import xbmcaddon

import xbmc

# Limits for truncation when generating fallback representations
_TRUNC_URL = 20
_TRUNC_REPR = 400


def init_logger() -> logging.Logger:
    """Initializes the file and Kodi log handlers."""

    try:
        addon = xbmcaddon.Addon()
        log_path = addon.getSetting("log_path") or "/storage/.kodi/temp/sendspin-service.log"
    except Exception:
        log_path = "/storage/.kodi/temp/sendspin-service.log"

    logger = logging.getLogger("sendspin")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    # File handler (best-effort)
    try:
        fh = logging.FileHandler(log_path)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass

    # Kodi handler to surface logs in Kodi's log
    class KodiHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                level = xbmc.LOGINFO if record.levelno < 40 else xbmc.LOGERROR
                xbmc.log(f"[Sendspin] {self.format(record)}", level)
            except Exception:
                pass

    kh = KodiHandler()
    kh.setFormatter(fmt)
    logger.addHandler(kh)
    logger.propagate = False
    return logger


# --- Small sanitiser / truncator helpers ------------------------------------


def _truncate_url(url: Any, max_len: int = _TRUNC_URL) -> str:
    """Shortens URLs to keep log entries readable."""
    try:
        if not url:
            return ""
        s = str(url)
        if len(s) <= max_len:
            return s
        return s[:max_len] + "...(truncated)"
    except Exception:
        return "<bad-url>"


def _truncate_repr(s: str | None, max_len: int = _TRUNC_REPR) -> str:
    """Truncates object strings to a safe maximum length."""
    if s is None:
        return "None"
    try:
        if len(s) <= max_len:
            return s
        return s[:max_len] + "...(truncated)"
    except Exception:
        return "<unrepresentable>"


def _deep_clean_payload(obj: Any) -> Any:
    """Recursively removes UndefinedFields and converts objects to clean dictionaries."""
    try:
        if obj is None or isinstance(obj, (bool, int, float, str)):
            return obj
        if isinstance(obj, (bytes, bytearray)):
            return f"<bytes len={len(obj)}>"

        # Handle UndefinedField sentinel type
        if type(obj).__name__ == "UndefinedField":
            return None

        # Handle Enum-like objects (e.g. PlayerCommand)
        enum_val = _try_enum_or_name(obj)
        if enum_val:
            return enum_val

        if isinstance(obj, dict):
            return {k: _deep_clean_payload(v) for k, v in obj.items() if type(v).__name__ != "UndefinedField"}

        if isinstance(obj, (list, tuple, set)):
            return [_deep_clean_payload(v) for v in obj if type(v).__name__ != "UndefinedField"]

        if hasattr(obj, "__dict__"):
            data = {}
            for k, v in vars(obj).items():
                # Skip private attributes and UndefinedFields
                if k.startswith("_") or type(v).__name__ == "UndefinedField":
                    continue
                if k in ("artwork_url", "art", "artwork", "image_url"):
                    data[k] = _truncate_url(v)
                else:
                    data[k] = _deep_clean_payload(v)
            return data

        return str(obj)
    except Exception:
        return "<cleanup-failed>"


# --- Payload detail extraction --------------------------------


def _try_enum_or_name(val: Any) -> str | None:
    """Extracts a string name from Enum or Class-based constants."""
    try:
        if hasattr(val, "name"):
            return str(val.name)
    except Exception:
        pass
    return None


def _format_payload_pretty(payload: Any, indent_level: int = 1) -> str:
    """Formats a payload into a multi-line string with grouped duplicates and indentation."""
    try:
        cleaned = _deep_clean_payload(payload)

        # If the result isn't a dictionary (like a single string or number), return it simply
        if not isinstance(cleaned, dict):
            return f"\n  - {cleaned}"

        # Combine duplicates: group keys that share the same value
        val_map = {}
        for k, v in cleaned.items():
            v_str = str(v)
            val_map.setdefault(v_str, []).append(k)

        lines = []
        base_indent = "  " * indent_level
        for v_str, keys in val_map.items():
            key_label = ", ".join(keys)
            lines.append(f"{base_indent}{key_label}: {v_str}")

        return "\n" + "\n".join(lines)
    except Exception:
        return " <pretty-format-failed>"


# --- Listener Setup ------------------------------------------------


def _internal_log(log: logging.Logger, label: str, name: str, payload: Any):
    """Formats and writes listener event data to the active loggers."""
    try:
        if isinstance(payload, (bytes, bytearray)):
            log.info("%s %s: binary len=%d", label, name, len(payload))
            return
        ptype = type(payload).__name__ if payload is not None else "None"
        details = _format_payload_pretty(payload)
        log.info("%s %s (%s): %s", label, name, ptype, details)
    except Exception:
        pass


def setup_client_listeners(
    client: object,
    handlers: dict[str, callable],
    log: logging.Logger | None = None,
    mode: str = "unhandled",
    exclude: set[str] | None = None,
):
    """
    Inspects the client for available 'add_*_listener' methods.
    Wraps and attaches logging handlers to each listener based on the specified "mode":
        "all", "unhandled", or "none"
    also can exclude specific listeners via the exclude set.
    """
    if log is None:
        log = logging.getLogger("sendspin")

    mode = mode.lower()
    exclude = exclude or set()

    for attr in dir(client):
        # 1. Filter for listener setters only
        if not attr.startswith("add_") or "listener" not in attr:
            continue
        if attr in exclude:
            continue

        user_handler = handlers.get(attr)
        is_handled = user_handler is not None

        # 2. Determine if we should attach based on ATTACH_MODE
        should_attach_auto = (mode == "all") or (mode == "unhandled" and not is_handled)

        if not is_handled and not should_attach_auto:
            continue

        try:
            setter = getattr(client, attr)
            if not callable(setter):
                continue

            label = "[HANDLED]\n" if is_handled else "[UNHANDLED]"

            def make_final_handler(name: str, callback: callable | None, lbl: str):
                def _unified_callback(payload: Any = None, *a, **kw):
                    result = None
                    # A) Execute your service logic first (e.g., on_metadata)
                    if callback:
                        try:
                            result = callback(payload, *a, **kw)
                        except Exception:
                            # Catch but log exceptions so the service keeps running
                            log.exception("User handler for %s failed", name)

                    # B) Log the event using the unified format
                    _internal_log(log, lbl, name, payload)

                    # C) Return the original result
                    return result

                return _unified_callback

            # Register the new wrapper with the client
            setter(make_final_handler(attr, user_handler, label))
            log.debug("Configured listener: %s as %s", attr, label)

        except Exception:
            log.debug("Failed to setup listener for %s", attr)
