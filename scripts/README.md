# Ticket Tailor reconcilers

Ticket Tailor has no shared inventory between ticket types: nothing stops two
ticket types selling the same seat twice. Both scripts here fix that the same
way, by recomputing every allocation from scratch once a day and writing it
back, which makes them idempotent and self-correcting after refunds and voids.

| Script | Course | Series | The clash it resolves |
|---|---|---|---|
| `reconcile.py` | Seeds of English | `es_2383043` | a semester pack vs the months it covers |
| `reconcile_courses.py` | Atelier de Criatividade | `es_2387994` | monthly places vs per-session drop-ins |
| `reconcile_courses.py` | Uma Onda na Mente | `es_2389589` | monthly places vs per-session drop-ins |

`reconcile_courses.py` replaced `reconcile_atelier.py` on 2026-08-31, when a
third course needed identical arithmetic. The maths did not change; the config
moved out of module scope into a `COURSES` list. Adding a fourth
monthly-plus-drop-in course means appending a `Course(...)` there and nothing
else: not a new file, not a new workflow step.

`ttlib.py` holds the auth, the HTTP call and `write_payload()`, shared so the
sale-window workaround below can never diverge between the two.

## Seeds of English — pack vs months

    month_total[M] = CAPACITY[M] - (packs sold covering M)
    seats_free[M]  = month_total[M] - month_issued[M]
    pack_total[P]  = pack_issued[P] + min(seats_free[M] for M in P)

Mon 7 Sep 2026 11:00 to Mon 1 Feb 2027 12:30, weekly Monday mornings. Five paid
months at EUR 66 (September to January) and one `Curso Completo (Set-Fev)` pack
at EUR 313.50. The single 1 Feb session is covered by January's ticket, so
there is no February ticket type.

## Atelier de Criatividade and Uma Onda na Mente — monthly vs drop-in

A monthly ticket takes a seat in EVERY session of its month; a dated drop-in
takes a seat in ONE session. For each month M and each future session d in M:

    month_total[M]  = CAPACITY[M] - max(dropin_issued[d] for d in future(M))
    dropin_total[d] = CAPACITY[M] - month_issued[M]

A new monthly buyer must sit in every remaining session, so the busiest one
binds: hence the `max()`. A new drop-in buyer sits in one session, but every
monthly student is already in it: hence subtracting `month_issued`.

Only future sessions count. A session that has happened cannot constrain a new
monthly buyer, who could not have attended it, so it drops out of the `max()`
as the month goes on. Past sessions and finished months are never written to.

Mon 7 Sep 2026 17:30 to Mon 28 Jun 2027 19:00, weekly Monday afternoons. Ten
monthly tickets at EUR 75 and 43 dated drop-ins at EUR 20, one per Monday.

## Running them

    cd scripts
    python3 reconcile.py                    # dry run, prints intended changes
    python3 reconcile.py --apply            # writes them
    python3 reconcile_courses.py            # same, every drop-in course
    python3 reconcile_courses.py --apply
    python3 reconcile_courses.py --course onda        # just one course
    python3 reconcile_courses.py --today 2027-01-11   # pretend it is that day

    python3 test_reconcile_courses.py       # offline; no key, no network

Every course is reconciled even when an earlier one fails, and the exit code is
1 if any of them was oversold or had a stale config. That is why there is one
workflow step for them rather than one per course.

The workflow lives at `.github/workflows/reconcile.yml` in the **repo root**,
not beside the script. GitHub only reads workflows from the root.

In CI it reads the `TT_API_KEY` repo secret. Locally it falls back to the key
file in the Semente folder. It exits non-zero if any month is oversold, so a
failed Actions run is a real alarm, not noise.

## House rules

**The script owns every allocation.** Anything written to a `quantity` field by
hand, in the dashboard or over the API, is overwritten on the next run.

To hold seats back for in-person students, lower that month's entry in
`CAPACITY`. That is the only sanctioned way, and it works because the reserved
seat lives inside the config the script computes from. September is set to 5
(not the room's 10) for exactly this reason.

`CAPACITY` therefore means *seats offered online*, which is not the same as room
capacity. Note the trade-off: seats held back this way leave no trace in Ticket
Tailor's own reports.

Issuing placeholder tickets via `POST /issued_tickets` was the previous house
rule and is no longer used. That endpoint now returns
`400 - "You need to add your billing details or have enough ticket credits
before you can import tickets."` anyway.

## Changing a course

Everything lives in the config block at the top of each script.

`reconcile.py`: `SERIES`, `MONTHS` (ordered `(name, ticket_type_id)`),
`CAPACITY` (keyed by those month names) and `PACKS`
(`ticket_type_id -> (display name, [month names])`). Pack months are listed
explicitly; nothing is inferred from the pack's name.

`reconcile_courses.py`: a `COURSES` list of `Course` objects, each with
`key` (the `--course` name), `name`, `series`, `months` (ordered
`(month key, display name, ticket_type_id)` with keys like `2027-01`),
`capacity` (keyed by those month keys) and `sessions` (ordered
`(YYYY-MM-DD, ticket_type_id)`, one line per session). A session's month is
taken from its own date, so nothing has to be kept in step by hand.

`capacity` is seats offered ONLINE, not room capacity. The Atelier's ten-seat
room is set to 8 because two places were taken in person. Uma Onda is set to
10, from Ines's course document; **her spreadsheet says 8 and she has not
confirmed which is right.**

**Cancelling a session** means deleting its line from `SESSIONS` *and* deleting
the ticket type. Removing only the line leaves it quietly on sale; removing
only the ticket type trips `check_config()`, which is the safer of the two
mistakes.

`check_config()` runs before any write and aborts if a configured ticket type
no longer exists, a month is missing from `CAPACITY`, `CAPACITY` names a month
that is gone, a pack covers an unknown month, a session falls in an unknown
month, a date is listed twice, or a month has no sessions at all. Shortening a
course is exactly the change that trips this, which is the point.

## Tests

`test_reconcile_courses.py` swaps in a fake box office and captures writes
instead of sending them, so it needs no key and no network and cannot touch the
live account. It covers the arithmetic both ways round, past sessions dropping
out, both oversold paths exiting 1, stale config aborting before any write, the
dry run writing nothing, and every write carrying its sale window.

Every scenario runs against **both** courses at their own real capacity, so a
change that is right for the Atelier and wrong for Uma Onda cannot pass. It
also checks the cross-course invariants (no duplicate keys, series or ticket
ids) and that `main()` still reconciles a healthy course when another one's
config is broken.

Run it after any edit to the maths, the config, or `write_payload()`.

## Never POST a bare quantity

Ticket Tailor shifts `hide_until` and `hide_after` forward by exactly one hour
on any partial update that **omits** them. A bare `{"quantity": N}` write walks
that ticket's sale window an hour later every time it runs. Confirmed
2026-08-28: four quantity writes moved one cutoff four hours late.

A field sent explicitly is stored exactly as sent, so `write_payload()` echoes
both timestamps back alongside the quantity. Every write goes through it. If you
add a new write path, route it through `write_payload()` too.

The same trap applies by hand: editing a price, name or fee over the API moves
the window unless you re-send both timestamps in the same call. Always read
`hide_until` / `hide_after` back after any ticket-type write.

## Keeping the schedule alive

GitHub disables scheduled workflows after 60 days of repository inactivity, and
this repo rarely gets commits. The workflow therefore commits a `.keepalive`
file at the repo root once per calendar month. It compares the file's contents
to the current `YYYY-MM`, so it commits exactly once a month however often it
runs.

It runs with `if: always()`, so a failing reconcile cannot be what kills the
schedule.
