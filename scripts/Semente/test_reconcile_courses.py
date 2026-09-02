#!/usr/bin/env python3
"""
Offline tests for reconcile_courses.py.

Substitutes a fake box office for ttlib.ticket_types and captures every write
instead of sending it, so this NEVER touches the live Ticket Tailor account
and needs no API key or network.

    python3 test_reconcile_courses.py

Every scenario runs against BOTH courses, at each course's own capacity, so a
config that is right for the Atelier and wrong for Uma Onda cannot pass.

Exit 0 means every case passed. Run it after any edit to the maths, to a
course config, or to write_payload.
"""

import contextlib
import io
import sys

import ttlib
import reconcile_courses as R

writes = []
failures = []


def check(label, cond, detail=""):
    print("%-4s %s%s" % ("ok" if cond else "FAIL", label,
                         "" if cond else "  <- " + detail))
    if not cond:
        failures.append(label)


def fake_box_office(course, cap, month_sales=None, dropin_sales=None,
                    totals=None, drop_ids=None):
    """Every ticket type at allocation cap, with the given sales."""
    month_sales = month_sales or {}
    dropin_sales = dropin_sales or {}
    totals = totals or {}
    tts = {}
    for key, name, tid in course.months:
        tts[tid] = {"id": tid, "name": name,
                    "quantity_issued": month_sales.get(key, 0),
                    "quantity_total": totals.get(tid, cap),
                    "hide_until": {"unix": 111}, "hide_after": {"unix": 222}}
    for date, tid in course.sessions:
        if drop_ids is not None and tid not in drop_ids:
            continue
        tts[tid] = {"id": tid, "name": "Sessao " + date,
                    "quantity_issued": dropin_sales.get(date, 0),
                    "quantity_total": totals.get(tid, cap),
                    "hide_until": {"unix": 333}, "hide_after": {"unix": 444}}
    return tts


def run_course(course, cap, today, apply_changes=True, **kw):
    """Reconcile one course against a fake box office."""
    del writes[:]
    tts = fake_box_office(course, cap, **kw)
    R.ticket_types = lambda key, series: tts
    R.request = lambda key, path, data=None: writes.append((path, data))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        healthy = R.reconcile(course, "sk_fake",
                              __import__("datetime").date.fromisoformat(today),
                              apply_changes)
    return healthy, out.getvalue(), list(writes)


def wrote(ws, tid):
    for path, data in ws:
        if path.endswith(tid):
            return data
    return None


# Each course at its own real capacity, so the arithmetic is checked against
# the number that will actually be live.
CASES = [
    (R.ATELIER, 8,  "2027-01"),
    (R.ONDA,    10, "2027-01"),
]

for course, CAP, MONTH in CASES:
    print("\n--- %s (capacity %d) ---" % (course.name, CAP))
    sess_in_month = [d for d, _ in course.sessions if d.startswith(MONTH)]
    TID = dict(course.sessions)
    MONTH_TID = {k: tid for k, _, tid in course.months}
    first = sess_in_month[0]
    later = sess_in_month[2]
    day = first                      # "today" = the month's first session

    # 1. Quiet box office: nothing sold, everything already at capacity.
    healthy, out, ws = run_course(course, CAP, day)
    check("quiet box office writes nothing", ws == [] and healthy, repr(ws))
    check("quiet box office says so", "Nothing to change." in out)

    # 2. Three monthly students eat three seats in EVERY session of the month,
    #    so every future drop-in in it drops by three. The month's own
    #    allocation is untouched, because no drop-in has been sold.
    healthy, out, ws = run_course(course, CAP, day, month_sales={MONTH: 3})
    future = [d for d in sess_in_month if d >= day]
    check("3 monthly -> every future drop-in falls to cap-3",
          all(wrote(ws, TID[d]) and wrote(ws, TID[d])["quantity"] == CAP - 3
              for d in future),
          repr([(d, wrote(ws, TID[d])) for d in future]))
    check("3 monthly -> monthly allocation unchanged",
          wrote(ws, MONTH_TID[MONTH]) is None)
    check("3 monthly -> healthy", healthy)

    # 3. Two drop-ins on one session. Only the busiest session constrains a
    #    new monthly buyer, so the month falls by two; drop-in allocations do
    #    not move, because no monthly ticket has been sold.
    healthy, out, ws = run_course(course, CAP, day, dropin_sales={later: 2})
    check("2 drop-ins -> monthly falls to cap-2",
          wrote(ws, MONTH_TID[MONTH])
          and wrote(ws, MONTH_TID[MONTH])["quantity"] == CAP - 2,
          repr(wrote(ws, MONTH_TID[MONTH])))
    check("2 drop-ins -> drop-in allocations unchanged",
          all(wrote(ws, TID[d]) is None for d in future))

    # 4. Both at once. The busiest session then holds 3 monthly + 2 drop-in,
    #    which must fit inside capacity. That is the point of the whole script.
    healthy, out, ws = run_course(course, CAP, day, month_sales={MONTH: 3},
                                  dropin_sales={later: 2})
    check("mixed -> monthly cap-2",
          wrote(ws, MONTH_TID[MONTH])["quantity"] == CAP - 2)
    check("mixed -> drop-ins cap-3",
          all(wrote(ws, TID[d])["quantity"] == CAP - 3 for d in future))
    check("mixed -> busiest session seats 3+2 within capacity", 3 + 2 <= CAP)

    # 5. A drop-in sold for a session that has already happened must not hold
    #    a seat against future monthly buyers.
    healthy, out, ws = run_course(course, CAP, later,
                                  dropin_sales={first: CAP // 2})
    check("past drop-in does not constrain the month",
          wrote(ws, MONTH_TID[MONTH]) is None,
          repr(wrote(ws, MONTH_TID[MONTH])))

    # 6. Oversold both ways: the room is full of monthly students and a
    #    drop-in was sold anyway.
    healthy, out, ws = run_course(course, CAP, day, month_sales={MONTH: CAP},
                                  dropin_sales={later: 1})
    check("oversold is unhealthy", not healthy)
    check("oversold names the month", "oversold" in out, out[:200])
    check("oversold names the session", "%s oversold" % later in out, out[:400])

    # 7. A ticket type that has vanished from the box office must stop that
    #    course before anything is written.
    healthy, out, ws = run_course(course, CAP, day,
                                  drop_ids=set(list(TID.values())[:5]))
    check("stale config is unhealthy", not healthy)
    check("stale config writes nothing", ws == [], repr(ws))
    check("stale config names a missing session", "no longer exists" in out)

    # 8. Every write must carry the sale window, or Ticket Tailor walks it an
    #    hour later. This is the whole reason write_payload exists.
    healthy, out, ws = run_course(course, CAP, day, month_sales={MONTH: 3})
    check("every write pins hide_until and hide_after",
          ws and all("hide_until" in d and "hide_after" in d for _, d in ws),
          repr(ws[:2]))

    # 9. Dry run must never write.
    healthy, out, ws = run_course(course, CAP, day, apply_changes=False,
                                  month_sales={MONTH: 3})
    check("dry run writes nothing", ws == [], repr(ws))
    check("dry run says it is a dry run", "Dry run" in out)


print("\n--- shared and cross-course ---")

check("write_payload passes a null window through as absent",
      "hide_until" not in ttlib.write_payload({"hide_until": None}, 5))

check("every course key is unique",
      len({c.key for c in R.COURSES}) == len(R.COURSES))
check("every course series is unique",
      len({c.series for c in R.COURSES}) == len(R.COURSES))
for c in R.COURSES:
    check("%s: no ticket id used twice" % c.key,
          len({t for _, _, t in c.months} | {t for _, t in c.sessions})
          == len(c.months) + len(c.sessions))
    check("%s: every session falls in a configured month" % c.key,
          all(d[:7] in c.capacity for d, _ in c.sessions))


def run_main(argv, per_series):
    """Run main() across all courses with a per-series fake box office."""
    del writes[:]
    R.ticket_types = lambda key, series: per_series[series]
    R.api_key = lambda: "sk_fake"
    R.request = lambda key, path, data=None: writes.append((path, data))
    saved = sys.argv[:]
    sys.argv = ["reconcile_courses.py"] + argv
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out):
            R.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = saved
    return code, out.getvalue(), list(writes)


DAY = "2027-01-05"

quiet = {R.ATELIER.series: fake_box_office(R.ATELIER, 8),
         R.ONDA.series: fake_box_office(R.ONDA, 10)}
code, out, ws = run_main(["--apply", "--today", DAY], quiet)
check("main: quiet run exits 0 and writes nothing", code == 0 and ws == [],
      repr(ws))
for c in R.COURSES:
    check("main: %s appears in the output" % c.key, c.name in out)

# A stale config on the FIRST course must not stop the second from being
# reconciled. This is what the two always() workflow steps used to do.
broken = dict(quiet)
broken[R.ATELIER.series] = fake_box_office(
    R.ATELIER, 8, drop_ids=set(list(dict(R.ATELIER.sessions).values())[:5]))
onda_month = R.ONDA.months[3][0]
broken[R.ONDA.series] = fake_box_office(R.ONDA, 10,
                                        month_sales={onda_month: 3})
code, out, ws = run_main(["--apply", "--today", DAY], broken)
check("main: a broken course exits 1", code == 1)
check("main: the healthy course is still reconciled", ws != [], repr(ws))
check("main: nothing written for the broken course",
      all(R.ATELIER.series not in p for p, _ in ws), repr(ws[:3]))

code, out, ws = run_main(["--apply", "--today", DAY, "--course", "onda"],
                         quiet)
check("main: --course runs only that course",
      R.ONDA.name in out and R.ATELIER.name not in out)

print()
if failures:
    sys.exit("%d FAILED: %s" % (len(failures), ", ".join(failures)))
print("All cases passed.")
