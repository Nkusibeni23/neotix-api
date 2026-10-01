# Notes

## 1. Design

### Data model

```text
users ──< requests ──< request_status_events >── users (who made the change)
              │
              └──< assignments >── episodes
                   (episode_id is the primary key)
```

- **users**: email (unique), Argon2 password hash, role, organisation, active flag.
- **episodes**: one row per recording. `episode_id` from the recording system is **unique**, which
  is what makes the import safe to repeat.
- **requests**: who asked, task, how many episodes, deadline, notes, and the current status.
- **request_status_events**: one row for every status change (including creation): from, to, who,
  when. Never updated, only added to.
- **assignments**: which episode is on which request, who assigned it, when. `episode_id` is the
  **primary key**, so an episode can be on only one request.

**Where state lives:** everything that matters is in PostgreSQL. The API keeps nothing between
requests (JWT, no sessions). The browser only caches what the API returned, plus the token. Live
updates only say "request 12 changed"; the browser then reloads it through the normal endpoints,
so the database stays the only source of truth.

### The three hardest decisions

1. **How to guarantee an episode is on only one request.** If the API checks "is it free?" and then
   inserts, two operators clicking at the same moment could both pass the check. So the database
   enforces it: `assignments.episode_id` is the primary key, and a second insert simply fails. The
   API still checks first, but only to return a clear message. Status changes and assignments also
   lock the request row while they run, so "deliver" can't happen halfway through "remove an
   episode".

2. **What to do when the CSV has the same episode twice with different values.** EP-00011 is `bad`
   on one line and `good` on another. There's no way to know which is right. I went with: **the
   first one wins, and every later one is reported with the fields that differ.** It's the same
   rule as for re-imports (an episode already in the database is never overwritten), so there is
   one simple rule, and nothing changes without someone seeing it in the report. Rejecting both
   would be safer for data quality, and is an easy change if the team prefers it.

3. **Storing the current status and the history.** I could keep only the events and work out the
   current status from the latest one, but then every list and filter needs that calculation. So
   the status is a column (fast to filter), and the events table is the history. Both are written
   in the same transaction, so they can't disagree. The history also gives the analytics its
   "time to delivery" without extra columns.

### Choices where the brief was open

- Only clients create requests. Admins can do what operators do, but can't accept or reject for a
  client.
- Episodes can only be added or removed while a request is in progress.
- CSV: `14/08/2026` is read day-first; times without a timezone are UTC; `45.5` seconds is rejected
  rather than rounded, because I'd rather not invent data; blank lines are ignored.
- Analytics: "fulfilment" covers requests submitted in the date range, and the median uses the
  **first** delivery, so rework doesn't make delivery look faster.
- A client opening another client's request gets "not found" instead of "forbidden", so they can't
  even learn which request numbers exist.
- Stretch item chosen: **real-time** (Server-Sent Events).

## 2. Left out or simplified, and what I'd do next

- **No rate limiting**, including on login.
- **The browser keeps the token in `localStorage`**: simple, but any script on the page could read
  it.
- **No refresh tokens**: a token lasts 8 hours. Deactivating someone still works immediately,
  because the API reloads the user on every request.
- **Live updates assume one API process**: the list of open connections is kept in memory.
- **Two repositories**: `docker compose` downloads the frontend from GitHub, so the frontend repo
  must stay public.
- **The requests list isn't paginated** (fine at this size; the episode list is).

With two more days: send live updates through Postgres `LISTEN/NOTIFY` so they work with several
API processes; move the token to an httpOnly cookie; add login rate limiting; add CI that runs the
tests and builds the images; add an end-to-end browser test of the main flow.

## 3. Something that went wrong

**The project didn't start from a clean clone.** Everything worked on my machine, but when we did
what a reviewer would do (clone the API repository into an empty folder and run
`docker compose up`), it couldn't even fetch the frontend. The compose file and README pointed at
`dataset-request-desk-web` and `dataset-request-desk-api`, but my GitHub repositories are actually
called `neotix-test` and `neotix-api`, so both links were 404s.

How it was found: by checking the URLs directly (`curl` returned 404 for the names in the README,
200 for the real ones). The fix was two lines, but the lesson was bigger: "works on my machine"
isn't the same as "works for whoever clones it", and the only real test is a fresh clone. After
the fix, a clean clone came up with migrations, seed users, the imported episodes, a healthy API
and the frontend, with no manual steps.

A smaller one: a test for the median delivery time returned 0 hours instead of 4. The query was
fine; the test helper created requests that were "delivered" at the moment they were created, so
their first delivery really was after 0 hours. The helper now starts every request's history with
"submitted", like real data.

## 4. Security

- **Passwords**: hashed with Argon2, never stored or logged in plain text. A wrong password and an
  unknown email give the same error and take about the same time, so login doesn't reveal who has
  an account.
- **Tokens**: signed JWTs that expire; the secret comes from an environment variable. The live
  stream uses a separate 60-second token that doesn't work anywhere else, because it goes in a URL.
- **Authorization**: always on the server. Each endpoint checks the role, and clients are limited to
  their own requests. There are tests for clients and operators trying things they aren't allowed
  to do.
- **Input**: every request body is validated (positive numbers, length limits, allowed values);
  all SQL uses parameters; the database has its own CHECK and UNIQUE rules as a last line; the
  importer checks every field of every row.

The two risks I'd worry about most:

1. **One client reaching another client's data** by changing a number in a URL. That's why access
   is checked per request (not just per role), other clients' requests return "not found", and
   tests cover it.
2. **Token theft through injected scripts (XSS)**, because the token is in `localStorage`. React
   escapes what it shows and nothing renders raw HTML, but the proper fix is an httpOnly cookie
   plus a Content-Security-Policy.

## 5. Scale

I tested with **5 million episodes** (one year of data, about 1 GB). Through the real API on a
laptop: analytics for a month ~0.3 s, for a full year ~0.6 s; the episode picker ~0.14 s. Importing
the 5 million rows took about 10 minutes. The README has the details and query plans.

**With 100× the episodes, what breaks first:**

1. **Importing**: rows go in batches inside one long transaction; tens of millions would take over
   an hour. I'd load the file with `COPY` into a staging table, then insert from there, committing
   in chunks so an interrupted import can continue.
2. **Analytics over long periods**: the work grows with the number of episodes in the range. I'd
   keep a small daily summary table (per day, robot, task and quality), updated by the importer,
   so charts read thousands of rows instead of millions.
3. **The episode picker**: counting all matches and skipping to deep pages gets slow. I'd page by
   "after this id" instead of page numbers, and show an approximate total.

**With 10× the users:**

1. **Database connections** run out before CPU. I'd run several API processes behind a load
   balancer, with PgBouncer in front of Postgres.
2. **Live updates** then need Postgres `LISTEN/NOTIFY` (or Redis), so an event reaches browsers
   connected to any process.
3. **Login**: Argon2 is slow on purpose, so bursts of logins cost CPU; rate limiting helps here too.

## 6. AI tooling

I used **Claude Code** (Anthropic) for most of the implementation. It wrote the large majority of
the backend, frontend and tests, ran the checks (tests, type-checking, lint, builds, the clean-clone
Docker run and the 5-million-episode benchmark), and drafted this document and the READMEs.

My part: I set the direction and made the calls. I chose the stack for the frontend (Next.js,
Tailwind, shadcn/ui, TanStack) and asked for a clean, consistent, reusable design; I pushed the UX
to where I wanted it (light theme only, bigger buttons, confirmation dialogs wherever an action
can't be undone, a proper date picker, placeholders, numbered tables, better filters); I chose
real-time as the stretch item; and I managed the repositories, branches and pull requests. I also
caught problems by running it myself, and asked for fixes.

Bugs the AI introduced were caught by its own checks: its first migration declared each CHECK
constraint twice, and its first test helper broke the median calculation (section 3). Everything
here is code I've read and can explain and change.
