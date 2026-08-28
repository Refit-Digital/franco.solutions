# Ticket Tailor pack reconciler

Keeps Semente's Ticket Tailor pack availability in step with single-month
availability. Ticket Tailor has no shared inventory between ticket types, so a
multi-month pack cannot natively take a seat out of each month it covers. This
recomputes every allocation from scratch and writes it back, once a day.

    month_total[M] = CAPACITY[M] - (packs sold covering M)
    seats_free[M]  = month_total[M] - month_issued[M]
    pack_total[P]  = pack_issued[P] + min(seats_free[M] for M in P)

Recomputed from scratch each run, so it is idempotent and self-corrects after
refunds and voids.

## The course it manages

Seeds of English, series `es_2383043`. Mon 7 Sep 2026 11:00 to Mon 1 Feb 2027
12:30, weekly Monday mornings.

Five paid months at EUR 66 (September, October, November, December, January)
and one `Curso Completo (Set-Fev)` pack at EUR 313.50. The single 1 Feb session
is covered by January's ticket, so there is no February ticket type.

## Running it

    cd scripts
    python3 reconcile.py            # dry run, prints intended changes
    python3 reconcile.py --apply    # writes them

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

## Changing the course

`SERIES`, `MONTHS`, `CAPACITY` and `PACKS` at the top of `reconcile.py`.

- `MONTHS` is an ordered list of `(name, ticket_type_id)`.
- `CAPACITY` is a dict keyed by those same month names.
- `PACKS` is `ticket_type_id -> (display name, [month names])`. Months are
  listed explicitly; nothing is inferred from the pack's name.

`check_config()` runs before any write and aborts if a configured ticket type
no longer exists, a month is missing from `CAPACITY`, `CAPACITY` names a month
that is gone, or a pack covers an unknown month. Shortening the course is
exactly the change that trips this, which is the point.

After creating a ticket type, always read `hide_until` / `hide_after` back. The
create endpoint has been seen to store them an hour off; re-POSTing the same
timestamp to the update endpoint fixes it.

## Keeping the schedule alive

GitHub disables scheduled workflows after 60 days of repository inactivity, and
this repo rarely gets commits. The workflow therefore commits a `.keepalive`
file at the repo root once per calendar month. It compares the file's contents
to the current `YYYY-MM`, so it commits exactly once a month however often it
runs.

It runs with `if: always()`, so a failing reconcile cannot be what kills the
schedule.
