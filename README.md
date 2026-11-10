# truetask-db

Read and write a Truetask database from Python, with the methods you already know from the PocketBase SDK.

A Truetask database is a table: `Customers`, `Invoices`, `Shots`.
The rest of the API shows one as cards plus separate custom-field values plus separate links, so reading a row means joining three resources.
This client talks to the records API instead, where a row is a flat dict and a filter is the PocketBase grammar.

```python
from truetask_db import Truetask

tt = Truetask("https://tasks.example.com", "ttdb_...")
customers = tt.database("8cv0q1w2e3r4t5y")

page = customers.get_list(1, 30, filter=tt.filter(
    "Status = {:status} && Amount > {:floor}", status="Active", floor=10,
), sort="-Amount", expand="Contact")

for row in page["items"]:
    print(row["title"], row["Amount"], row["expand"]["Contact"][0]["title"])
```

It needs nothing outside the standard library and runs on Python 3.7 and later, so it also works in the Python a DCC ships (mayapy, Nuke's, hython).

## Install

```bash
pip install truetask-db
```

It is not on PyPI yet.
Until it is, install it from a copy of this repository:

```bash
pip install ./truetask-client-python
```

## A Truetask, and a database

```python
tt = Truetask(url, credential, timeout=30)
rows = tt.database(database_id)
```

`url` is where Truetask answers, without a path.
`credential` is a database key, a visitor token minted with one, a personal access token or a session JWT (see below).
Both fall back to the `TRUETASK_URL` and `TRUETASK_API_TOKEN` environment variables, the names the `truetask` command line uses, so a script can carry neither.

A database's id is in the address bar when you open it, and in the Share dialog a key is minted from.

## Rows

A row is a flat dict: `id`, `key`, `title`, `created`, `updated`, and a value per column named the way the schema spells it.

```python
{"id": "re7k...", "key": "CUST-14", "title": "Acme", "created": "2026-11-10 13:24:49.676Z",
 "updated": "2026-11-10 13:25:02.118Z", "Status": "Active", "Amount": 1200.5,
 "Markets": ["EU", "US"], "Contact": ["ab12..."]}
```

A column name is matched loosely wherever you write one, so `unit_price`, `unitprice` and `Unit price` all reach the same column, whether you are filtering, sorting, trimming or writing.
An empty cell reads back as `None`, a relation column with nothing linked included.

`PER_PAGE_MAX` and `COMPUTED_PER_PAGE_MAX` are the two page caps, importable so a caller can name them instead of writing 500 and 100.

### `get_list(page=1, per_page=30, filter="", sort="", expand="", fields="")`

One page: `{"page", "perPage", "totalItems", "totalPages", "items"}`.

```python
page = customers.get_list(2, 100, filter='Status = "Active"', sort="-Amount,title")
page["totalItems"]   # every row the filter matches, not the page
```

- `per_page` is at most 500, and at most 100 when `fields` names a rollup or a formula: each of those is computed per row as it is read.
- `sort` takes any stored column, `-` for descending. Numbers sort numerically, dates by date, text case-insensitively, and an empty cell sorts last whichever way the column runs. The default is `created`, oldest first, so paging is stable.
- `fields` trims the columns a row carries, and is the only way to include a rollup or a formula.
- `expand` resolves a relation column's targets to `{"id", "title", "key"}` under the row's own `expand` key. The ids stay in the column itself either way. A target in a database the credential cannot see stays an id.

A filter on a column is answered by the API over the whole database, so it refuses one past 10,000 live rows and says so.
`id`, `title`, `created` and `updated` page without reading it, so a plain `sort="-created"` walk is cheap at any size.

### `get_full_list(batch=500, **options)`

Every row the filter matches, as one list, read `batch` at a time.
Takes the same `filter`, `sort`, `expand` and `fields`.

```python
everyone = customers.get_full_list(filter='Status = "Active"', sort="title")
```

Pass `batch=COMPUTED_PER_PAGE_MAX` when `fields` names a rollup or a formula column.
Rows inserted while this is paging may be missed or seen twice, the same as any paged read.

### `get_first_list_item(filter, **options)`

The first row the filter matches, or `TruetaskError(404)`.
`sort` decides which row is first, and without one it is the oldest.

```python
try:
    who = customers.get_first_list_item(tt.filter("Email = {:e}", e=address))
except TruetaskError as err:
    if err.status != 404:
        raise
    who = None
```

### `get_one(id, expand="", fields="")`

One row by its id, in the same shape the list returns.
A row in the trash or the archive is not one: that answers 404.

### `create(data)`

Insert one row and answer it, re-read after the write.

```python
made = customers.create({
    "title": "Acme", "Status": "Active", "Amount": 1200.5,
    "Due": "2026-01-31", "Markets": ["EU", "US"], "Contact": [contact_id],
})
```

`title` names the record and every other key is a column.
Values are typed per column: a number as a number, a checkbox as a bool, a date as `"2026-01-31"`, a select as one of its choices, a duration as `"1h 30m"` or seconds, a people column as user ids, a relation as the target records' ids.

### `update(id, data)`

Change the columns `data` names and answer the row, re-read.
A column it does not name is left alone.
`None` clears a cell, and a relation column takes the list of target ids it should end up with, so `[]` unlinks it.

```python
customers.update(made["id"], {"Amount": 14, "Notes": None, "Contact": []})
```

### `delete(id)`

Move one row to the database's trash, where it can be restored.
Answers `None`.

### `visitors.create(external_id="")`

Mint an identity for one browser, with a key that holds the `visitors` right.
Answers `{"id", "token", "external_id", "expires", "created"}`; the token is in that answer and in no other.
See [Visitors](#visitors).

```python
minted = comments.visitors.create()
mine = Truetask(URL, minted["token"]).database(comments.id)
mine.create({"title": "Nice post"})
```

### `schema()`

The columns, so a program knows what to send and expect.

```python
{"id": "...", "name": "Customers", "key_prefix": "CUST", "people_database": False,
 "fields": [{"id": "...", "name": "Status", "type": "select",
             "options": {"choices": ["Active", "Paused"]},
             "computed": False, "writable": True, "filterable": True, "sortable": True}]}
```

A computed column (a rollup, a formula, a created or updated stamp) is read and never written.

## Visitors

A key is one credential for a whole site, so every program and every browser holding it is the same caller.
A **visitor** is an identity for one of them, minted with a key that has the `visitors` right: you keep the token and send it where the key went.

```python
URL = "https://tasks.example.com"
comments = Truetask(URL, "ttdb_...").database(COMMENTS)

minted = comments.visitors.create()              # or create(external_id=user.id)
mine = Truetask(URL, minted["token"]).database(COMMENTS)

mine.create({"title": "Nice post", "Body": "Thanks for writing this."})
```

A visitor carries the key's rights, the key's requests a minute and the key's expiry, and the key's own `visitor_days` decides how long it lasts (90 days by default).
Revoking the key invalidates every visitor it minted, at once.

What it adds is ownership:

- a row the visitor inserted is stamped with it, and comes back carrying `mine: True`;
- `filter="mine = true"` is that visitor's own rows;
- with the key set to **Own rows**, `update` and `delete` reach only those rows. Another visitor's row answers 404, the same as a row that is not there.

A visitor cannot mint another visitor, and a visitor token reaches nothing in Truetask but this one database's rows.

```python
rows = mine.get_full_list(filter="mine = true", sort="-created")
mine.update(rows[0]["id"], {"Body": "Edited."})
```

`external_id` binds the visitor to your own user id, which a workspace reader then sees beside the row.
It is a label and not a proof: a key sitting in a browser lets anybody send anybody's id, so bind one only when you mint from your **server**.

To forget a visitor, which is the "delete my account" a site owes somebody who asks, call the route with the visitor's own token, or with the key that minted it:

```python
mine.client.request("DELETE", "db/{}/visitors/{}".format(COMMENTS, minted["id"]))
```

The identity goes and the rows stay, with no owner, so on an Own rows key nobody may change them again.

## The filter

The grammar is PocketBase's:

```
Status = "Active" && Amount > 10
(Status = "Active" || Status = "Paused") && Notes !~ "draft"
Owner = null
`Unit price` > 3
```

- Operators are `= != > >= < <= ~ !~`, where `~` is a case-insensitive contains, joined with `&&` and `||` and grouped with parentheses.
- `null` asks for an empty cell.
- `id`, `title`, `created` and `updated` are built in; every other name is a column, and one with spaces goes in backticks.
- A multi select, a people column or a relation matches when ANY of its entries does, and a relation matches on the target record's id.
- A column with no order (a checkbox, a select) refuses `>`; a number refuses `~`.
- A filter the server cannot read answers 400 saying what is wrong, where, and which columns there are.

### Binding values

Never paste a value into a filter yourself: a quote or a backslash in somebody's name changes what the filter asks.
`Truetask.filter` writes the literal for you.

```python
tt.filter("Status = {:s} && Amount > {:floor}", s="Active", floor=10)
# 'Status = "Active" && Amount > 10'

tt.filter("title = {:t}", t='Acme "the" Bros')
# 'title = "Acme \\"the\\" Bros"'
```

| A value | Becomes |
| --- | --- |
| `str` | a double-quoted literal, with `\` and `"` escaped |
| `int`, `float`, `Decimal` | the number written out in full, never `1e-05` |
| `True`, `False` | `true`, `false` |
| `None` | `null`, which asks for an empty cell |
| `datetime.date` | `"YYYY-MM-DD"`, the shape a date column stores |
| `datetime.datetime` | `"YYYY-MM-DD HH:MM:SS"` in UTC, the shape a stamp stores |

A placeholder with nothing passed for it raises `KeyError`; a value a filter has no word for raises `TypeError`.
Spare keywords are ignored, so one dict of values can serve several filters.
There is no placeholder for a set of values: write `(c = {:a} || c = {:b})`.

It is a static method, so `Truetask.filter(...)` and `tt.filter(...)` are the same call.

## Errors

Everything the server refuses, and every call that could not be made, raises one `TruetaskError`.

```python
from truetask_db import TruetaskError

try:
    customers.create({"title": "Acme", "Status": "Nope"})
except TruetaskError as err:
    err.status        # 422
    err.message       # "'Status': not one of its choices: Active, Paused"
    err.response      # the parsed body, {} when there was none
    err.retry_after   # seconds, when the answer carried Retry-After
```

| Status | What it means |
| --- | --- |
| 0 | the request could never be made: no address, no credential, nothing listening |
| 400 | the filter, the sort, `fields` or `expand` cannot be read, or the database is too large to filter on a column |
| 401 | the credential is not valid, has expired or was revoked |
| 403 | the credential may not do that here, or the rows are a People database's |
| 404 | not a database, not a database this credential reaches, or no live row with that id |
| 422 | an unknown column, a computed column, or a value the column cannot hold |
| 429 | over the credential's limit; `retry_after` says how long to wait |

## Credentials

- A **database key** (`ttdb_...`) is minted in a database's Share dialog by a board owner or admin. It reaches that one database and nothing else: another database answers 404, and every other `/v1` route refuses it. What it may do (read, insert, update, delete, hand out visitors), when it expires and how many requests a minute it gets are set when it is minted. This is the one to put in a backend or a scheduled script.
- A **visitor token** (`ttv_...`) is minted with such a key by `visitors.create()`, and is one browser's or one program's own identity inside that database. It does what the key does, and the rows it inserts are its own. See [Visitors](#visitors).
- A **personal access token** (`tt_...`) acts as the person who made it, with their board roles and their scope. Fine for a notebook or a one-off script of your own; never hand one to somebody else's program.
- A **session JWT** is what a signed-in app holds. It works here too, and expires like any session.

A Truetask whose Share dialog offers no keys yet is on an older version: a personal token is the credential there.

A **People** database lists the workspace's people on its own, so its rows are never written through the API: a write there answers 403.

Never put a credential in a file you commit.
Read it from the environment, or from whatever your deployment uses for secrets.

## What this does not do

No async client, no realtime subscriptions, no file upload.
A row's attachments and a database's realtime stream are the app's own surfaces for now.

## Tests

```bash
python3 -m unittest discover -s tests
```

They run the client against a small HTTP server in the same process, so they need no Truetask and no network.
