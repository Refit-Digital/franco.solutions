#!/usr/bin/env python3
"""
Semente / Ticket Tailor: keep pack availability and single-month availability
in step.

Ticket Tailor has no shared inventory between ticket types, so a multi-month
pack cannot natively take a seat out of each month it covers. This recomputes
every allocation from scratch on each run and writes it back, which makes it
idempotent and self-correcting after refunds and voids.

Maths, per run:
    month_total[M] = CAPACITY[M] - (packs sold that cover M)
    seats_free[M]  = month_total[M] - month_issued[M]
    pack_total[P]  = pack_issued[P] + min(seats_free[M] for M in P)

CAPACITY is the number of seats offered ONLINE for that month, which is not
always the room capacity. The room holds 10. September is set to 5 because
five places were taken in person and Franco chose (2026-08-28) to hold them
back by lowering the ceiling rather than issuing placeholder tickets, the
Ticket Tailor import endpoint having started refusing new issued tickets
without billing details or ticket credits.

That is the ONLY sanctioned way to reserve seats outside the box office, and
it works only because it lives here. This script owns the allocation field:
anything written there by hand, in the dashboard or over the API, is
overwritten on the next run. To reserve a seat, edit CAPACITY.

Note: the Ticket Tailor API returns 403 to urllib without a User-Agent header.

Usage:
    python3 reconcile.py            # dry run, prints intended changes
    python3 reconcile.py --apply    # writes them
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request

API = "https://api.tickettailor.com/v1"
SERIES = "es_2383043"          # Seeds of English

# Month ticket types, in course order. The course runs Mon 7 Sep 2026 to
# Mon 1 Feb 2027; the single 1 Feb session is covered by January's ticket,
# so there is no February ticket type.
MONTHS = [
    ("September", "tt_6734522"),
    ("October",   "tt_6734594"),
    ("November",  "tt_6734597"),
    ("December",  "tt_6734598"),
    ("January",   "tt_6734599"),
]

# Seats offered online per month. See the note on CAPACITY above.
CAPACITY = {
    "September": 5,
    "October":   10,
    "November":  10,
    "December":  10,
    "January":   10,
}

# Pack ticket types: id -> (display name, months covered).
# One pack survives: it now spans the whole shortened course.
PACKS = {
    "tt_6738684": ("Curso Completo (Set-Fev)",
                   ["September", "October", "November", "December", "January"]),
}


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
    auth = __import__("base64").b64encode(("%s:" % key).encode()).decode()
    req.add_header("Authorization", "Basic " + auth)
    req.add_header("User-Agent", "semente-reconcile/1.0")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def ticket_types(key):
    data = request(key, "/event_series")
    for series in data["data"]:
        if series["id"] == SERIES:
            return {t["id"]: t for t in series["default_ticket_types"]}
    sys.exit("Event series %s not found" % SERIES)


def check_config(tts):
    """Fail loudly if the config has drifted from the live box office."""
    problems = []
    for name, tid in MONTHS:
        if tid not in tts:
            problems.append("month %s (%s) no longer exists" % (name, tid))
        if name not in CAPACITY:
            problems.append("month %s has no CAPACITY entry" % name)
    known = {n for n, _ in MONTHS}
    for name in CAPACITY:
        if name not in known:
            problems.append("CAPACITY has stale month %s" % name)
    for ptid, (pname, months) in PACKS.items():
        if ptid not in tts:
            problems.append("pack %s (%s) no longer exists" % (pname, ptid))
        for m in months:
            if m not in known:
                problems.append("pack %s covers unknown month %s" % (pname, m))
    if problems:
        for p in problems:
            print("ERROR: %s" % p)
        sys.exit("Config does not match the live event series. Fix reconcile.py.")


def main():
    apply_changes = "--apply" in sys.argv
    key = api_key()
    tts = ticket_types(key)
    check_config(tts)

    issued = {name: tts[tid]["quantity_issued"] for name, tid in MONTHS}

    # How many sold packs cover each month.
    covered = {name: 0 for name, _ in MONTHS}
    pack_issued = {}
    for ptid, (pname, months) in PACKS.items():
        sold = tts[ptid]["quantity_issued"]
        pack_issued[ptid] = sold
        for m in months:
            covered[m] += sold

    month_total = {n: CAPACITY[n] - covered[n] for n, _ in MONTHS}
    seats_free = {n: month_total[n] - issued[n] for n, _ in MONTHS}

    oversold = False
    changes = []
    for name, tid in MONTHS:
        want = month_total[name]
        have = tts[tid]["quantity_total"]
        if want < issued[name]:
            oversold = True
            print("ERROR: %s oversold. %d sold but only %d seats after packs."
                  % (name, issued[name], want))
        if want != have:
            changes.append((name, tid, have, want))

    for ptid, (pname, months) in PACKS.items():
        want = pack_issued[ptid] + min(seats_free[m] for m in months)
        have = tts[ptid]["quantity_total"]
        if want != have:
            changes.append((pname, ptid, have, want))

    print("%-24s %5s %6s %5s" % ("", "sold", "free", "alloc"))
    for name, _ in MONTHS:
        print("%-24s %5d %6d %5d" % (name, issued[name], seats_free[name],
                                     month_total[name]))
    print()

    if not changes:
        print("Nothing to change.")
        if oversold:
            sys.exit(1)
        return

    for name, tid, have, want in changes:
        print("%-24s %s  %d -> %d" % (name, tid, have, want))

    if not apply_changes:
        print("\nDry run. Re-run with --apply to write these.")
        if oversold:
            sys.exit(1)
        return

    for name, tid, have, want in changes:
        request(key, "/event_series/%s/ticket_types/%s" % (SERIES, tid),
                {"quantity": want})
        print("wrote %s = %d" % (name, want))

    if oversold:
        sys.exit(1)


if __name__ == "__main__":
    main()
