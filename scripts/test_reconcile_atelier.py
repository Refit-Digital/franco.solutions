#!/usr/bin/env python3
"""
Offline tests for reconcile_atelier.py.

Substitutes a fake box office for ttlib.ticket_types and captures every write
instead of sending it, so this NEVER touches the live Ticket Tailor account
and needs no API key or network.

    python3 test_reconcile_atelier.py

Exit 0 means every case passed. Run it after any edit to the maths, the
config, or write_payload.
"""

import contextlib
import io
import sys

import ttlib
import reconcile_atelier as R

CAP = 8
writes = []


def fake_box_office(month_sales=None, dropin_sales=None, totals=None,
                    drop_ids=None):
    """Every ticket type at allocation CAP with the given sales."""
    month_sales = month_sales or {}
    dropin_sales = dropin_sales or {}
    totals = totals or {}
    tts = {}
    for key, name, tid in R.MONTHS:
        tts[tid] = {"id": tid, "name": name,
                    "quantity_issued": month_sales.get(key, 0),
                    "quantity_total": totals.get(tid, CAP),
                    "hide_until": {"unix": 111}, "hide_after": {"unix": 222}}
    for date, tid in R.SESSIONS:
        if drop_ids is not None and tid not in drop_ids:
            continue
        tts[tid] = {"id": tid, "name": "Sessao " + date,
                    "quantity_issued": dropin_sales.get(date, 0),
                    "quantity_total": totals.get(tid, CAP),
                    "hide_until": {"unix": 333}, "hide_after": {"unix": 444}}
    return tts


def run(today, **kw):
    """Run main() with a fake box office. Returns (exit_code, output, writes)."""
    del writes[:]
    tts = fake_box_office(**kw)
    R.ticket_types = lambda key, series: tts
    R.api_key = lambda: "sk_fake"
    R.request = lambda key, path, data=None: writes.append((path, data))
    argv = sys.argv[:]
    sys.argv = ["reconcile_atelier.py", "--apply", "--today", today]
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out):
            R.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = argv
    return code, out.getvalue(), list(writes)


def wrote(ws, tid):
    for path, data in ws:
        if path.endswith(tid):
            return data
    return None


failures = []


def check(label, cond, detail=""):
    print("%-4s %s%s" % ("ok" if cond else "FAIL", label,
                         "" if cond else "  <- " + detail))
    if not cond:
        failures.append(label)


JAN = "2027-01"
JAN_SESSIONS = [d for d, _ in R.SESSIONS if d.startswith(JAN)]
JAN_TID = dict(R.SESSIONS)
MONTH_TID = {k: tid for k, _, tid in R.MONTHS}

# 1. Quiet box office: nothing sold, everything already at 8.
code, out, ws = run("2026-09-01")
check("quiet box office writes nothing", ws == [] and code == 0, repr(ws))
check("quiet box office says so", "Nothing to change." in out)

# 2. Three monthly students in January eat three seats in every January
#    session, so every future January drop-in drops to 5. January's own
#    allocation is untouched, because no drop-in has been sold.
code, out, ws = run("2027-01-05", month_sales={JAN: 3})
future_jan = [d for d in JAN_SESSIONS if d >= "2027-01-05"]
check("3 monthly -> every future Jan drop-in 8->5",
      all(wrote(ws, JAN_TID[d]) and wrote(ws, JAN_TID[d])["quantity"] == 5
          for d in future_jan),
      repr([(d, wrote(ws, JAN_TID[d])) for d in future_jan]))
check("3 monthly -> Jan monthly allocation unchanged",
      wrote(ws, MONTH_TID[JAN]) is None)
check("3 monthly -> exit 0", code == 0)

# 3. Two drop-ins on one January Monday. Only the busiest session constrains a
#    new monthly buyer, so January falls to 6; the drop-in allocations do not
#    move, because no monthly ticket has been sold.
code, out, ws = run("2027-01-05", dropin_sales={"2027-01-18": 2})
check("2 drop-ins -> Jan monthly 8->6",
      wrote(ws, MONTH_TID[JAN]) and wrote(ws, MONTH_TID[JAN])["quantity"] == 6,
      repr(wrote(ws, MONTH_TID[JAN])))
check("2 drop-ins -> drop-in allocations unchanged",
      all(wrote(ws, JAN_TID[d]) is None for d in future_jan))

# 4. Both at once. January monthly = 8 - 2 = 6; every future January drop-in
#    = 8 - 3 = 5. The busiest session then holds 3 monthly + 2 drop-in = 5 of
#    8, which is the answer we want and not a coincidence of the arithmetic.
code, out, ws = run("2027-01-05", month_sales={JAN: 3},
                    dropin_sales={"2027-01-18": 2})
check("mixed -> Jan monthly 6",
      wrote(ws, MONTH_TID[JAN])["quantity"] == 6)
check("mixed -> Jan drop-ins 5",
      all(wrote(ws, JAN_TID[d])["quantity"] == 5 for d in future_jan))
check("mixed -> busiest session seats 3+2 within capacity of 8",
      3 + 2 <= CAP)

# 5. A drop-in sold for a session that has already happened must not hold a
#    seat against future monthly buyers.
code, out, ws = run("2027-01-20", dropin_sales={"2027-01-04": 4})
check("past drop-in does not constrain the month",
      wrote(ws, MONTH_TID[JAN]) is None, repr(wrote(ws, MONTH_TID[JAN])))

# 6. Oversold both ways: the room is full of monthly students and a drop-in
#    was sold anyway.
code, out, ws = run("2027-01-05", month_sales={JAN: 8},
                    dropin_sales={"2027-01-18": 1})
check("oversold exits 1", code == 1)
check("oversold names the month", "January oversold" in out, out)
check("oversold names the session", "2027-01-18 oversold" in out, out)

# 7. A ticket type that has vanished from the box office must stop the run
#    before anything is written.
code, out, ws = run("2026-09-01", drop_ids=set(list(JAN_TID.values())[:5]))
check("stale config exits 1", code == 1)
check("stale config writes nothing", ws == [], repr(ws))
check("stale config names a missing session", "no longer exists" in out)

# 8. Every write must carry the sale window, or Ticket Tailor walks it an hour
#    later. This is the whole reason write_payload exists.
code, out, ws = run("2027-01-05", month_sales={JAN: 3})
check("every write pins hide_until and hide_after",
      ws and all("hide_until" in d and "hide_after" in d for _, d in ws),
      repr(ws[:2]))
check("write_payload passes a null window through as absent",
      "hide_until" not in ttlib.write_payload({"hide_until": None}, 5))

# 9. Dry run must never write.
del writes[:]
tts = fake_box_office(month_sales={JAN: 3})
R.ticket_types = lambda key, series: tts
R.api_key = lambda: "sk_fake"
R.request = lambda key, path, data=None: writes.append((path, data))
argv = sys.argv[:]
sys.argv = ["reconcile_atelier.py", "--today", "2027-01-05"]
with contextlib.redirect_stdout(io.StringIO()) as out:
    R.main()
sys.argv = argv
check("dry run writes nothing", writes == [], repr(writes))
check("dry run says it is a dry run", "Dry run" in out.getvalue())

print()
if failures:
    sys.exit("%d FAILED: %s" % (len(failures), ", ".join(failures)))
print("All cases passed.")
