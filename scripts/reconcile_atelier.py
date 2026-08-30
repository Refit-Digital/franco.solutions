#!/usr/bin/env python3
"""
Semente / Ticket Tailor: keep Atelier de Criatividade's monthly places and its
per-session drop-in places from overselling the room.

Atelier de Criatividade sells two things against the same seats. A monthly
ticket takes one seat in EVERY session of that month; a dated drop-in ticket
takes one seat in ONE session. Ticket Tailor has no shared inventory between
ticket types, so nothing stops 8 monthly places and 8 drop-in places being
sold into a room that holds 10. This recomputes every allocation from scratch
each run, which makes it idempotent and self-correcting after refunds, voids
and cancellations.

Maths, per run, for each month M and each future session d in M:

    month_total[M]  = CAPACITY[M] - max(dropin_issued[d] for d in future(M))
    dropin_total[d] = CAPACITY[M] - month_issued[M]

Read those as "what is left after the other side has taken its seats".

    A new monthly buyer has to sit in every remaining session of the month, so
    the binding constraint is the busiest one: hence the max().

    A new drop-in buyer only has to sit in one session, but every monthly
    student is already in it: hence subtracting month_issued.

Both are TOTAL allocations, not remaining. Ticket Tailor recalculates
remaining as total minus issued, so writing the total is enough and the script
never has to subtract what the box office already counted as sold.

Only FUTURE sessions are considered. A session that has already happened
cannot constrain a new monthly buyer, who could not attend it anyway, so its
drop-in sales drop out of the max() as the month progresses. Past sessions and
finished months are never written to.

CAPACITY is the number of seats offered ONLINE for that month, which is not
the room capacity. The room holds 10 and every month is set to 8, because two
places were taken in person. As in reconcile.py, that is the ONLY sanctioned
way to reserve seats outside the box office: this script owns the allocation
field, and anything written there by hand, in the dashboard or over the API,
is overwritten on the next run. To reserve a seat, edit CAPACITY.

Usage:
    python3 reconcile_atelier.py            # dry run, prints intended changes
    python3 reconcile_atelier.py --apply    # writes them
    python3 reconcile_atelier.py --today 2027-01-11   # pretend it is that day
"""

import datetime
import sys

from ttlib import api_key, request, ticket_types, write_payload

SERIES = "es_2387994"          # Atelier de Criatividade

# Monthly ticket types: (month key, display name, ticket type id).
# The course runs Mon 7 Sep 2026 to Mon 28 Jun 2027, weekly on Mondays.
MONTHS = [
    ("2026-09", "September", "tt_6744842"),
    ("2026-10", "October",   "tt_6744843"),
    ("2026-11", "November",  "tt_6744844"),
    ("2026-12", "December",  "tt_6744845"),
    ("2027-01", "January",   "tt_6744846"),
    ("2027-02", "February",  "tt_6744847"),
    ("2027-03", "March",     "tt_6744848"),
    ("2027-04", "April",     "tt_6744849"),
    ("2027-05", "May",       "tt_6744850"),
    ("2027-06", "June",      "tt_6744851"),
]

# Seats offered online per month. See the note on CAPACITY above.
CAPACITY = {
    "2026-09": 8, "2026-10": 8, "2026-11": 8, "2026-12": 8, "2027-01": 8,
    "2027-02": 8, "2027-03": 8, "2027-04": 8, "2027-05": 8, "2027-06": 8,
}

# Dated drop-in ticket types: (session date, ticket type id). One per Monday.
# Franco chose (2026-08-30) not to skip any date, including 5 Oct 2026
# (Implantacao da Republica), the Christmas Mondays, Carnival and Easter. If a
# session is cancelled, remove its line here AND pull the ticket type, or the
# script will keep it on sale.
SESSIONS = [
    ("2026-09-07", "tt_6744873"), ("2026-09-14", "tt_6744874"),
    ("2026-09-21", "tt_6744875"), ("2026-09-28", "tt_6744876"),
    ("2026-10-05", "tt_6744877"), ("2026-10-12", "tt_6744878"),
    ("2026-10-19", "tt_6744879"), ("2026-10-26", "tt_6744880"),
    ("2026-11-02", "tt_6744881"), ("2026-11-09", "tt_6744882"),
    ("2026-11-16", "tt_6744883"), ("2026-11-23", "tt_6744884"),
    ("2026-11-30", "tt_6744885"), ("2026-12-07", "tt_6744886"),
    ("2026-12-14", "tt_6744887"), ("2026-12-21", "tt_6744888"),
    ("2026-12-28", "tt_6744889"), ("2027-01-04", "tt_6744890"),
    ("2027-01-11", "tt_6744891"), ("2027-01-18", "tt_6744892"),
    ("2027-01-25", "tt_6744893"), ("2027-02-01", "tt_6744894"),
    ("2027-02-08", "tt_6744895"), ("2027-02-15", "tt_6744896"),
    ("2027-02-22", "tt_6744897"), ("2027-03-01", "tt_6744898"),
    ("2027-03-08", "tt_6744899"), ("2027-03-15", "tt_6744900"),
    ("2027-03-22", "tt_6744901"), ("2027-03-29", "tt_6744902"),
    ("2027-04-05", "tt_6744903"), ("2027-04-12", "tt_6744904"),
    ("2027-04-19", "tt_6744905"), ("2027-04-26", "tt_6744906"),
    ("2027-05-03", "tt_6744907"), ("2027-05-10", "tt_6744908"),
    ("2027-05-17", "tt_6744909"), ("2027-05-24", "tt_6744910"),
    ("2027-05-31", "tt_6744911"), ("2027-06-07", "tt_6744912"),
    ("2027-06-14", "tt_6744913"), ("2027-06-21", "tt_6744914"),
    ("2027-06-28", "tt_6744915"),
]


def today():
    """Today, or the date given by --today YYYY-MM-DD, for testing."""
    if "--today" in sys.argv:
        return datetime.date.fromisoformat(sys.argv[sys.argv.index("--today") + 1])
    return datetime.date.today()


def check_config(tts):
    """Fail loudly if the config has drifted from the live box office."""
    problems = []
    keys = {k for k, _, _ in MONTHS}
    for key, name, tid in MONTHS:
        if tid not in tts:
            problems.append("month %s (%s) no longer exists" % (name, tid))
        if key not in CAPACITY:
            problems.append("month %s has no CAPACITY entry" % key)
    for key in CAPACITY:
        if key not in keys:
            problems.append("CAPACITY has stale month %s" % key)
    seen = set()
    for date, tid in SESSIONS:
        if tid not in tts:
            problems.append("session %s (%s) no longer exists" % (date, tid))
        if date[:7] not in keys:
            problems.append("session %s falls in unknown month %s" % (date, date[:7]))
        if date in seen:
            problems.append("session %s listed twice" % date)
        seen.add(date)
    for key in keys:
        if not any(d[:7] == key for d, _ in SESSIONS):
            problems.append("month %s has no sessions" % key)
    if problems:
        for p in problems:
            print("ERROR: %s" % p)
        sys.exit("Config does not match the live event series. "
                 "Fix reconcile_atelier.py.")


def main():
    apply_changes = "--apply" in sys.argv
    now = today()
    key = api_key()
    tts = ticket_types(key, SERIES)
    check_config(tts)

    month_issued = {k: tts[tid]["quantity_issued"] for k, _, tid in MONTHS}
    month_name = {k: n for k, n, _ in MONTHS}
    dropin_issued = {d: tts[tid]["quantity_issued"] for d, tid in SESSIONS}

    future = [(d, tid) for d, tid in SESSIONS
              if datetime.date.fromisoformat(d) >= now]
    future_by_month = {}
    for d, tid in future:
        future_by_month.setdefault(d[:7], []).append((d, tid))

    oversold = False
    changes = []

    for key_m, name, tid in MONTHS:
        sessions = future_by_month.get(key_m, [])
        if not sessions:
            continue                      # month is over; leave it alone
        busiest = max(dropin_issued[d] for d, _ in sessions)
        want = CAPACITY[key_m] - busiest
        if want < month_issued[key_m]:
            oversold = True
            print("ERROR: %s oversold. %d monthly sold but only %d seats "
                  "after drop-ins." % (name, month_issued[key_m], want))
        if want != tts[tid]["quantity_total"]:
            changes.append((name, tid, tts[tid]["quantity_total"], want))

    for d, tid in future:
        want = CAPACITY[d[:7]] - month_issued[d[:7]]
        if want < dropin_issued[d]:
            oversold = True
            print("ERROR: session %s oversold. %d drop-ins sold but only %d "
                  "seats after monthly students." % (d, dropin_issued[d], want))
        if want != tts[tid]["quantity_total"]:
            changes.append(("drop-in %s" % d, tid,
                            tts[tid]["quantity_total"], want))

    print("as at %s\n" % now)
    print("%-12s %8s %8s %9s %8s" % ("month", "monthly", "busiest", "sessions",
                                     "alloc"))
    for key_m, name, tid in MONTHS:
        sessions = future_by_month.get(key_m, [])
        if not sessions:
            print("%-12s %8d %8s %9s %8s" % (name, month_issued[key_m], "-",
                                             "past", "-"))
            continue
        busiest = max(dropin_issued[d] for d, _ in sessions)
        print("%-12s %8d %8d %9d %8d" % (name, month_issued[key_m], busiest,
                                         len(sessions),
                                         CAPACITY[key_m] - busiest))
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
                write_payload(tts[tid], want))
        print("wrote %s = %d" % (name, want))

    if oversold:
        sys.exit(1)


if __name__ == "__main__":
    main()
