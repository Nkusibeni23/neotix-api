# Notes

## 1. Design

### The data

```text
users ──< requests ──< request_status_events >── users (who made the change)
              │
              └──< assignments >── episodes
```

There are five tables.

- `users` holds the email, the hashed password, the role and whether the account is active.
- `episodes` has one row per recording. The `episode_id` from the recording system is unique.
  That is the reason the import can be run again without making duplicates.
- `requests` holds who asked, the task, how many episodes, the deadline, the notes and the current
  status.
- `request_status_events` gets a new row every time a status changes, with who did it and when.
  Rows are only added, never changed.
- `assignments` links an episode to a request. Its primary key is the episode, so one episode
  cannot be on two requests.

All the real data is in PostgreSQL. The API does not remember anything between calls, it only
checks the token. The browser keeps a copy of what the API sent, and the token. A live update only
says "request 12 changed" and then the browser asks the API for it again, so the database is
always the one place that is right.

### The three decisions I found hardest

**One episode, one request.** The simple way is to check in the API if the episode is free and
then save it. The problem is two operators can click at the same second and both pass the check.
So the rule is in the database: the episode is the primary key of `assignments`, and the second save
just fails. The API still checks first, but only so it can give a clear message. For the same
reason, a request is locked while its status changes or episodes are added, so "deliver" cannot
happen in the middle of "remove an episode".

**The same episode twice in the CSV, with different values.** EP-00011 is `bad` on one line and
`good` on another. I cannot know which one is true. I decided the first one wins, and the later
one goes in the report with the fields that are different. It is the same rule as importing a file
again: what is already in the database is never overwritten. So there is one rule, and a person
sees every conflict. Rejecting both lines would be safer for quality, and it is a small change if
the team wants that.

**Keeping the status and the history.** I could store only the history and work out the status
from the last row. But then every list and every filter has to do that work. So the status is a
normal column, which is fast to filter, and the history is its own table. Both are saved in the
same transaction, so they cannot disagree. The history also gives me "time to delivery" for the
analytics without adding more columns.

### Where the brief was open, I chose

- Only clients create requests. An admin can do what an operator does, but cannot accept or reject
  for a client.
- Episodes can be added or removed only while a request is in progress.
- In the CSV, `14/08/2026` is read as day first. A time with no timezone is UTC. A duration of
  `45.5` is rejected, not rounded, because I do not want to invent data. Empty lines are ignored.
- In analytics, "fulfilment" means requests submitted in the date range. The median uses the first
  delivery, so rework does not make it look faster.
- If a client opens another client's request, the answer is "not found", not "forbidden". That way
  they cannot even find out which request numbers exist.
- The stretch item I picked is real-time, with Server-Sent Events.

## 2. Left out or simplified, and what I'd do next

I stayed inside what the brief asks for. Where a simple option was enough for an internal tool, I
took it.

- There is one access token and it lasts 8 hours. There are no refresh tokens. Deactivating a user
  or changing a role still works at once, because the API loads the user again on every call.
- Live updates run inside the one API process that Compose starts. That is enough for this setup.
- The frontend and the API are in two repositories. `docker compose up` in the API repository
  downloads and builds the frontend, so it is still one command.
- The requests list has no pages, because it is small. The episode list has pages.
- There is no rate limiting in the app. In production I would put it on the reverse proxy.

With two more days I would do these, in this order:

1. Send live updates through Postgres `LISTEN/NOTIFY`, so they work with more than one API process.
2. Move the token to an httpOnly cookie.
3. Add rate limiting on login.
4. Add a browser test of the main flow to the CI. The CI already runs lint, the tests and the
   Docker build.

## 3. Something that went wrong

The project did not start from a fresh clone.

On my machine everything worked. Then it was tested the way a reviewer would do it: clone the API
repository into an empty folder and run `docker compose up`. It could not download the frontend.
The compose file and the README used the names `dataset-request-desk-web` and
`dataset-request-desk-api`, but my repositories on GitHub are called `neotix-test` and
`neotix-api`. Both links gave 404.

It was found by checking the links directly with `curl`: 404 for the names in the README, 200 for
the real names. The fix was two lines. What I took from it is that "it works on my machine" and
"it works for the person who clones it" are not the same thing, and the only way to know is to
try a fresh clone. After the fix, a fresh clone came up by itself: migrations, seed users, the
episodes, the API and the frontend.

A smaller one. A test for the median delivery time gave 0 hours and it should give 4. The query
was correct. The test data was wrong: the helper created requests that were already "delivered"
at the moment they were created, so the first delivery really was after 0 hours. Now the helper
starts every request with "submitted", like a real one.

## 4. Security

Passwords are hashed with Argon2. They are never saved or logged as plain text. A wrong password
and an email that does not exist give the same error and take about the same time, so the login
does not tell anyone who has an account.

Tokens are signed JWTs with an expiry. The secret comes from an environment variable. The live
stream uses another token that lasts 60 seconds and works only for the stream, because it has to
go in the URL.

Permissions are checked on the server, always. Every endpoint checks the role, and a client only
gets their own requests. There are tests where a client and an operator try things they are not
allowed to do.

Input is checked before it is used: numbers must be positive, text has a maximum length, and
status and role must be one of the allowed values. All SQL uses parameters. The database has its
own rules too (unique and check constraints). The importer checks every field of every row.

The two things I would worry about most in a system like this:

1. A client reaching another client's data by changing the number in the URL. That is why the API
   checks who owns each request, not only the role. Other clients' requests return "not found",
   and there are tests for it.
2. A stolen token through a script injected in the page (XSS), because the token is in
   `localStorage`. React escapes what it shows and the app does not render raw HTML. The better
   fix is an httpOnly cookie and a Content-Security-Policy.

## 5. Scale

The app was tested with 5 million episodes, which is one year of data and about 1 GB. Through the
real API on a laptop, analytics took about 0.3 s for one month and 0.6 s for the whole year. The
episode list took about 0.14 s. Importing the 5 million rows took about 10 minutes. The README has
the details.

With 100 times more episodes, these break first:

1. The import. It saves in batches inside one long transaction. Tens of millions of rows would
   take more than an hour. I would load the file with `COPY` into a temporary table and insert
   from there, saving in parts so a stopped import can continue.
2. Analytics for long periods. The work grows with the number of episodes in the date range. I
   would keep a small table with one row per day, robot, task and quality, updated by the import.
   Then the charts read thousands of rows, not millions.
3. The episode list. Counting all the matches and jumping to far pages gets slow. I would page by
   "after this id" and show an approximate total.

With 10 times more users:

1. Database connections run out before the CPU does. I would run several API processes behind a
   load balancer and put PgBouncer in front of Postgres.
2. Live updates would then need Postgres `LISTEN/NOTIFY` or Redis, so an update reaches a browser
   connected to any process.
3. Login. Argon2 is slow on purpose, so many logins at once use a lot of CPU. Rate limiting helps
   with this too.

## 6. AI tooling

I used Claude Code (Anthropic). I told it what I wanted, it wrote the code, the tests and the
first version of the docs, and I checked the result and asked for changes.

I chose the stack, how the app should look and behave, and real-time as the stretch item. The
domain rules and the import rules were worked out with the tool, and I agreed them. Section 1
explains the reasons. Everything can be checked again from the repository: the tests, a fresh
`docker compose up`, the 5 million episode test, and a script that tries every forbidden action
against the running API.
