#!/usr/bin/env python3
"""
Semente / Ticket Tailor: keep every course's monthly places and its per-session
drop-in places from overselling the room.

Generalised from reconcile_atelier.py on 2026-08-31, when a third course
(Uma Onda na Mente) needed the identical arithmetic. The maths did not change;
only the config moved out of module scope into COURSES. reconcile.py still
handles Seeds of English separately, because a multi-month PACK is a different
shape from a per-session drop-in.

A course here sells two things against the same seats. A monthly ticket takes
one seat in EVERY session of that month; a dated drop-in ticket takes one seat
in ONE session. Ticket Tailor has no shared inventory between ticket types, so
nothing stops 10 monthly places and 10 drop-in places being sold into a room
that holds 10. This recomputes every allocation from scratch each run, which
makes it idempotent and self-correcting after refunds, voids and cancellations.

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

capacity is the number of seats offered ONLINE for that month, which is not
necessarily the room capacity. As in reconcile.py, editing it is the ONLY
sanctioned way to reserve seats outside the box office: this script owns the
allocation field, and anything written there by hand, in the dashboard or over
the API, is overwritten on the next run.

Every course is reconciled even if an earlier one fails, so one bad config
cannot leave another course unprotected. The exit code is 1 if ANY course was
oversold or had a stale config.

Usage:
    python3 reconcile_courses.py                     # dry run, all courses
    python3 reconcile_courses.py --apply             # write them
    python3 reconcile_courses.py --course onda       # just one course
    python3 reconcile_courses.py --today 2027-01-11  # pretend it is that day
"""

import datetime
import sys

from ttlib import api_key, request, ticket_types, write_payload


class Course:
    """One course's box-office layout. See COURSES below."""

    def __init__(self, key, name, series, months, capacity, sessions):
        self.key = key
        self.name = name
        self.series = series
        self.months = months        # [(month key, display name, ticket id)]
        self.capacity = capacity    # {month key: seats offered online}
        self.sessions = sessions    # [(ISO date, ticket id)]


ATELIER = Course(
    key="atelier",
    name="Atelier de Criatividade",
    series="es_2387994",
    # Mon 7 Sep 2026 to Mon 28 Jun 2027, weekly on Mondays.
    months=[
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
    ],
    # Room holds 10; every month is 8 because two places were taken in person.
    capacity={
        "2026-09": 8, "2026-10": 8, "2026-11": 8, "2026-12": 8, "2027-01": 8,
        "2027-02": 8, "2027-03": 8, "2027-04": 8, "2027-05": 8, "2027-06": 8,
    },
    # Franco chose (2026-08-30) not to skip any date, including 5 Oct 2026
    # (Implantacao da Republica), the Christmas Mondays, Carnival and Easter.
    # If a session is cancelled, remove its line here AND pull the ticket
    # type, or the script will keep it on sale.
    sessions=[
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
    ],
)

ONDA = Course(
    key="onda",
    name="Uma Onda na Mente",
    series="es_2389589",
    # Tue 6 Oct 2026 to Tue 29 Jun 2027, weekly on Tuesdays. 40 EUR/month,
    # 15 EUR per single session.
    months=[
        ("2026-10", "October",   "tt_6748054"),
        ("2026-11", "November",  "tt_6748055"),
        ("2026-12", "December",  "tt_6748057"),
        ("2027-01", "January",   "tt_6748058"),
        ("2027-02", "February",  "tt_6748059"),
        ("2027-03", "March",     "tt_6748060"),
        ("2027-04", "April",     "tt_6748061"),
        ("2027-05", "May",       "tt_6748062"),
        ("2027-06", "June",      "tt_6748063"),
    ],
    # 10 comes from Ines's course document (30 Aug). Her cursos-e-eventos
    # spreadsheet (22 Aug) says 8 for this course. The document is the newer
    # source and is what the box office was built from, but she has NOT
    # confirmed which is right. If she says 8, change these nine numbers and
    # the ticket quantities follow on the next run.
    capacity={
        "2026-10": 10, "2026-11": 10, "2026-12": 10, "2027-01": 10,
        "2027-02": 10, "2027-03": 10, "2027-04": 10, "2027-05": 10,
        "2027-06": 10,
    },
    # Every Tuesday in the range, including 22 and 29 Dec 2026, which are
    # almost certainly not teaching weeks. Nobody has confirmed the holiday
    # breaks. Remove a line here AND pull the ticket type to cancel a session.
    sessions=[
    ("2026-10-06", "tt_6748096"), ("2026-10-13", "tt_6748097"),
    ("2026-10-20", "tt_6748098"), ("2026-10-27", "tt_6748099"),
    ("2026-11-03", "tt_6748100"), ("2026-11-10", "tt_6748101"),
    ("2026-11-17", "tt_6748102"), ("2026-11-24", "tt_6748103"),
    ("2026-12-01", "tt_6748104"), ("2026-12-08", "tt_6748105"),
    ("2026-12-15", "tt_6748106"), ("2026-12-22", "tt_6748107"),
    ("2026-12-29", "tt_6748108"), ("2027-01-05", "tt_6748109"),
    ("2027-01-12", "tt_6748110"), ("2027-01-19", "tt_6748111"),
    ("2027-01-26", "tt_6748112"), ("2027-02-02", "tt_6748113"),
    ("2027-02-09", "tt_6748114"), ("2027-02-16", "tt_6748115"),
    ("2027-02-23", "tt_6748116"), ("2027-03-02", "tt_6748117"),
    ("2027-03-09", "tt_6748118"), ("2027-03-16", "tt_6748119"),
    ("2027-03-23", "tt_6748120"), ("2027-03-30", "tt_6748121"),
    ("2027-04-06", "tt_6748122"), ("2027-04-13", "tt_6748123"),
    ("2027-04-20", "tt_6748124"), ("2027-04-27", "tt_6748125"),
    ("2027-05-04", "tt_6748126"), ("2027-05-11", "tt_6748127"),
    ("2027-05-18", "tt_6748128"), ("2027-05-25", "tt_6748129"),
    ("2027-06-01", "tt_6748130"), ("2027-06-08", "tt_6748131"),
    ("2027-06-15", "tt_6748132"), ("2027-06-22", "tt_6748133"),
    ("2027-06-29", "tt_6748134"),
    ],
)

COURSES = [ATELIER, ONDA]


def today():
    """Today, or the date given by --today YYYY-MM-DD, for testing."""
    if "--today" in sys.argv:
        return datetime.date.fromisoformat(sys.argv[sys.argv.index("--today") + 1])
    return datetime.date.today()


def selected_courses():
    """All courses, or just the one named by --course."""
    if "--course" not in sys.argv:
        return COURSES
    want = sys.argv[sys.argv.index("--course") + 1]
    chosen = [c for c in COURSES if c.key == want]
    if not chosen:
        sys.exit("Unknown course %r. Known: %s"
                 % (want, ", ".join(c.key for c in COURSES)))
    return chosen


def check_config(course, tts):
    """Return a list of problems where the config has drifted from live."""
    problems = []
    keys = {k for k, _, _ in course.months}
    for key, name, tid in course.months:
        if tid not in tts:
            problems.append("month %s (%s) no longer exists" % (name, tid))
        if key not in course.capacity:
            problems.append("month %s has no capacity entry" % key)
    for key in course.capacity:
        if key not in keys:
            problems.append("capacity has stale month %s" % key)
    seen = set()
    for date, tid in course.sessions:
        if tid not in tts:
            problems.append("session %s (%s) no longer exists" % (date, tid))
        if date[:7] not in keys:
            problems.append("session %s falls in unknown month %s"
                            % (date, date[:7]))
        if date in seen:
            problems.append("session %s listed twice" % date)
        seen.add(date)
    for key in keys:
        if not any(d[:7] == key for d, _ in course.sessions):
            problems.append("month %s has no sessions" % key)
    return problems


def reconcile(course, key, now, apply_changes):
    """Reconcile one course. Returns True if it is healthy."""
    print("=" * 60)
    print("%s  (%s)  as at %s\n" % (course.name, course.series, now))

    tts = ticket_types(key, course.series)
    problems = check_config(course, tts)
    if problems:
        for p in problems:
            print("ERROR: %s" % p)
        print("\nConfig does not match the live event series. Nothing was "
              "written for this course. Fix reconcile_courses.py.")
        return False

    month_issued = {k: tts[tid]["quantity_issued"] for k, _, tid in course.months}
    dropin_issued = {d: tts[tid]["quantity_issued"] for d, tid in course.sessions}

    future = [(d, tid) for d, tid in course.sessions
              if datetime.date.fromisoformat(d) >= now]
    future_by_month = {}
    for d, tid in future:
        future_by_month.setdefault(d[:7], []).append((d, tid))

    oversold = False
    changes = []

    for key_m, name, tid in course.months:
        sessions = future_by_month.get(key_m, [])
        if not sessions:
            continue                      # month is over; leave it alone
        busiest = max(dropin_issued[d] for d, _ in sessions)
        want = course.capacity[key_m] - busiest
        if want < month_issued[key_m]:
            oversold = True
            print("ERROR: %s oversold. %d monthly sold but only %d seats "
                  "after drop-ins." % (name, month_issued[key_m], want))
        if want != tts[tid]["quantity_total"]:
            changes.append((name, tid, tts[tid]["quantity_total"], want))

    for d, tid in future:
        want = course.capacity[d[:7]] - month_issued[d[:7]]
        if want < dropin_issued[d]:
            oversold = True
            print("ERROR: session %s oversold. %d drop-ins sold but only %d "
                  "seats after monthly students." % (d, dropin_issued[d], want))
        if want != tts[tid]["quantity_total"]:
            changes.append(("drop-in %s" % d, tid,
                            tts[tid]["quantity_total"], want))

    print("%-12s %8s %8s %9s %8s" % ("month", "monthly", "busiest", "sessions",
                                     "alloc"))
    for key_m, name, tid in course.months:
        sessions = future_by_month.get(key_m, [])
        if not sessions:
            print("%-12s %8d %8s %9s %8s" % (name, month_issued[key_m], "-",
                                             "past", "-"))
            continue
        busiest = max(dropin_issued[d] for d, _ in sessions)
        print("%-12s %8d %8d %9d %8d" % (name, month_issued[key_m], busiest,
                                         len(sessions),
                                         course.capacity[key_m] - busiest))
    print()

    if not changes:
        print("Nothing to change.")
        return not oversold

    for name, tid, have, want in changes:
        print("%-24s %s  %d -> %d" % (name, tid, have, want))

    if not apply_changes:
        print("\nDry run. Re-run with --apply to write these.")
        return not oversold

    for name, tid, have, want in changes:
        request(key, "/event_series/%s/ticket_types/%s" % (course.series, tid),
                write_payload(tts[tid], want))
        print("wrote %s = %d" % (name, want))

    return not oversold


def main():
    apply_changes = "--apply" in sys.argv
    now = today()
    key = api_key()

    healthy = True
    for course in selected_courses():
        # Every course runs even if an earlier one failed, so one stale config
        # cannot leave another course's room unprotected.
        if not reconcile(course, key, now, apply_changes):
            healthy = False
        print()

    if not healthy:
        sys.exit(1)


if __name__ == "__main__":
    main()
