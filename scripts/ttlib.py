#!/usr/bin/env python3
"""
Shared Ticket Tailor plumbing for the reconcile scripts.

Extracted from reconcile.py on 2026-08-30, when a second course needed the
same three helpers. They live here rather than being copied because
write_payload() below encodes a bug workaround that must never diverge between
callers: a second, stale copy of it would silently reintroduce the sale-window
drift on whichever script did not get the fix.

Note: the Ticket Tailor API returns 403 to urllib without a User-Agent header.
"""

import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request

API = "https://api.tickettailor.com/v1"


def api_key():
    """Environment first (CI), then the key file wherever it happens to live."""
    key = os.environ.get("TT_API_KEY")
    if key:
        return key
    candidates = [
        "~/Desktop/Semente/Franco/API Keys.rtf",   # Franco's Mac
        "~/mnt/Semente/Franco/API Keys.rtf",       # Cowork session mount
    ]
    for c in candidates:
        path = os.path.expanduser(c)
        try:
            blob = open(path, "rb").read().decode("utf-8", "ignore")
        except OSError:
            continue
        m = re.search(r"sk_[A-Za-z0-9_]+", blob)
        if m:
            return m.group(0)
        sys.exit("No API key found in %s" % path)
    sys.exit("No TT_API_KEY set and no key file found in: %s"
             % ", ".join(candidates))


def request(key, path, data=None):
    url = API + path
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method="POST" if data else "GET")
    auth = base64.b64encode(("%s:" % key).encode()).decode()
    req.add_header("Authorization", "Basic " + auth)
    req.add_header("User-Agent", "semente-reconcile/1.0")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def ticket_types(key, series):
    """All ticket types of one event series, keyed by id.

    Read from default_ticket_types inside GET /event_series. There is no
    GET /event_series/{id}/ticket_types collection endpoint; it 404s.
    """
    data = request(key, "/event_series")
    for s in data["data"]:
        if s["id"] == series:
            return {t["id"]: t for t in s["default_ticket_types"]}
    sys.exit("Event series %s not found" % series)


def write_payload(tt, quantity):
    """Quantity plus the sale window, pinned.

    Ticket Tailor shifts hide_until and hide_after forward by exactly one hour
    on any partial update that omits them, so a bare {"quantity": N} write
    silently walks a ticket's sale window an hour later every single time.
    Confirmed 2026-08-28: four quantity writes moved one cutoff four hours.
    Echoing the values we just read back pins them, because a field sent
    explicitly is stored exactly as sent.

    Never POST to a ticket type without going through this.
    """
    data = {"quantity": quantity}
    for field in ("hide_until", "hide_after"):
        val = tt.get(field)
        if val:
            data[field] = val["unix"]
    return data
