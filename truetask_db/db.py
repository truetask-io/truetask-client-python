"""A Truetask database as rows, from Python.

A Truetask database is a table, and these are the methods you already know
from the PocketBase SDK, in Python form: `get_list`, `get_full_list`,
`get_first_list_item`, `get_one`, `create`, `update`, `delete`, plus a
`filter` helper that binds values so a quote in somebody's name cannot change
what the filter asks.

    from truetask_db import Truetask

    tt = Truetask("https://tasks.example.com", "ttdb_...")
    customers = tt.database("8cv0q1w2e3r4t5y")

    page = customers.get_list(1, 30, filter=tt.filter(
        "Status = {:status} && Amount > {:floor}", status="Active", floor=10,
    ), sort="-Amount", expand="Contact")

    for row in page["items"]:
        print(row["title"], row["Amount"])

It needs nothing outside the standard library and runs on Python 3.7 and
later, so it also works in the Python a DCC ships (mayapy, Nuke's, hython).
"""

import datetime
import decimal
import http.client
import json
import os
import platform
import re
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

URL_ENV = "TRUETASK_URL"
CREDENTIAL_ENV = "TRUETASK_API_TOKEN"
# urllib's own "Python-urllib/3.x" is refused by Cloudflare's browser check
# (error 1010) before the request reaches Truetask, so say what we are.
USER_AGENT = "truetask-db-python/{} Python/{}".format(__version__, platform.python_version())

#: The largest page the API will answer.
PER_PAGE_MAX = 500
#: The largest page the API will answer when `fields` names a rollup or a
#: formula column: each of those is computed per row as it is read. Pass it as
#: `get_full_list(batch=COMPUTED_PER_PAGE_MAX, fields=...)`.
COMPUTED_PER_PAGE_MAX = 100

_PLACEHOLDER_RE = re.compile(r"\{:([A-Za-z_][A-Za-z0-9_]*)\}")
_LIST_OPTIONS = ("filter", "sort", "expand", "fields")


class TruetaskError(Exception):
    """A request the API refused, or one that could not be made.

    `status` is the HTTP status, 0 when no answer came; `message` is what the
    API said; `response` is its parsed body, `{}` when there was none;
    `retry_after` is the seconds the `Retry-After` header asked for, which a
    429 carries and nothing else usually does.
    """

    def __init__(self, status, message, response=None, retry_after=None):
        Exception.__init__(self, message)
        self.status = status
        self.message = message
        self.response = response if response is not None else {}
        self.retry_after = retry_after


class Truetask(object):
    """A Truetask at `url`, reached with `credential`.

    The credential is a database key (`ttdb_...`), a personal access token or
    a session JWT: the server decides what each of them may do, and this client
    only carries it. Both arguments fall back to the TRUETASK_URL and
    TRUETASK_API_TOKEN environment variables, the names the `truetask` command
    line uses.

        tt = Truetask("https://tasks.example.com", "ttdb_...")
        rows = tt.database("8cv0q1w2e3r4t5y")
    """

    def __init__(self, url=None, credential=None, timeout=30):
        self.url = (url or os.environ.get(URL_ENV) or "").strip().rstrip("/")
        self.credential = (credential or os.environ.get(CREDENTIAL_ENV) or "").strip()
        self.timeout = timeout
        if not self.url:
            raise TruetaskError(0, "No Truetask address: pass url= or set " + URL_ENV)
        if not self.credential:
            raise TruetaskError(
                0, "No credential: pass credential= or set " + CREDENTIAL_ENV
            )

    def database(self, database_id):
        """The database with that id: a board whose `kind` is `database`.

        Its id is in the address bar when you open it, and in the Share dialog
        a key is minted from.
        """
        return Database(self, database_id)

    @staticmethod
    def filter(expr, **params):
        """A filter with its values bound: `{:name}` becomes the literal for
        the `name` keyword, escaped so it cannot end the string it sits in.

            tt.filter("Status = {:s} && Amount > {:floor}", s="Active", floor=10)
            # 'Status = "Active" && Amount > 10'

        Text is double-quoted with `\\` and `"` escaped, a number is written
        out in full (never `1e-05`, which the server's grammar has no word
        for), `True` and `False` become `true` and `false`, `None` becomes
        `null`, a `date` becomes `"YYYY-MM-DD"` and a `datetime` becomes
        `"YYYY-MM-DD HH:MM:SS"` in UTC, the shape Truetask stores.

        A placeholder with no keyword raises `KeyError`; a value of a type a
        filter cannot say raises `TypeError`. Spare keywords are ignored, so
        one dict of values can serve several filters.
        """

        def bind(match):
            name = match.group(1)
            if name not in params:
                raise KeyError(
                    "filter: nothing was passed for {{:{}}}".format(name)
                )
            return _literal(name, params[name])

        return _PLACEHOLDER_RE.sub(bind, str(expr))

    def request(self, method, path, body=None, params=None):
        """One call to `/v1/<path>`. Answers the decoded JSON, None for an
        empty answer, and raises TruetaskError when the API says no.

        Every method below goes through here, so the credential, the
        User-Agent and the error mapping have one home.
        """
        url = self.url + "/v1/" + path.lstrip("/")
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None if body is None else json.dumps(body).encode("utf-8")
        call = urllib.request.Request(url, data=data, method=method)
        call.add_header("Authorization", "Bearer " + self.credential)
        call.add_header("Accept", "application/json")
        call.add_header("User-Agent", USER_AGENT)
        if data is not None:
            call.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(call, timeout=self.timeout) as answer:
                status = answer.getcode()
                raw = answer.read()
        except urllib.error.HTTPError as err:
            raise _refused(err)
        # `http.client` raises its own for a truncated or malformed answer, and
        # those are not OSErrors: without them a flaky connection would come
        # out of this library as something other than a TruetaskError.
        except (urllib.error.URLError, http.client.HTTPException, OSError) as err:
            raise TruetaskError(
                0,
                "Could not reach {}: {}".format(self.url, getattr(err, "reason", err)),
            )
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            # A 2xx that is not JSON is something in front of Truetask
            # answering, a proxy's error page or a sign-in wall.
            raise TruetaskError(
                status,
                "{} answered {} but not JSON: {}".format(
                    self.url, status, raw[:200].decode("utf-8", "replace")
                ),
            )


class Database(object):
    """One database's rows. Made by `Truetask.database(id)`, not directly.

    A row is a flat dict: `id`, `key`, `title`, `created`, `updated` and a
    value per column, named the way the schema spells it. A column name is
    matched loosely, so `unit_price`, `unitprice` and "Unit price" all reach
    the same column, whether you are filtering, sorting or writing.
    """

    def __init__(self, client, database_id):
        self.client = client
        self.id = str(database_id or "").strip()
        if not self.id:
            raise TruetaskError(0, "No database id: pass the database's id.")
        self._base = "db/" + urllib.parse.quote(self.id, safe="")
        self._records = self._base + "/records"

    def __repr__(self):
        return "<truetask_db.Database {}>".format(self.id)

    def schema(self):
        """The columns, so a program knows what to send and expect.

        `{"id", "name", "key_prefix", "people_database", "fields"}`, each field
        `{"name", "type", "options", "computed", "writable", "filterable",
        "sortable"}`. A computed column (a rollup, a formula, a created or
        updated stamp) is read and never written.
        """
        return self.client.request("GET", self._base)

    def get_list(self, page=1, per_page=30, filter="", sort="", expand="", fields=""):
        """One page of rows.

        Answers `{"page", "perPage", "totalItems", "totalPages", "items"}`.

        `filter` is the grammar `Truetask.filter` writes for you. `sort` takes
        any stored column with `-` for descending (`"-Amount,title"`); the
        default is oldest first, so paging is stable. `expand` resolves a
        relation column's targets to `{"id", "title", "key"}` under the row's
        `expand` key. `fields` trims the columns a row carries, and is the only
        way to include a rollup or a formula, which is why a page asking for
        one is capped at 100 rows.
        """
        params = {"page": int(page), "per_page": int(per_page)}
        params.update(
            _options(filter=filter, sort=sort, expand=expand, fields=fields)
        )
        return self.client.request("GET", self._records, params=params)

    def get_full_list(self, batch=500, **options):
        """Every row the filter matches, as one list, read `batch` at a time.

        Takes the same `filter`, `sort`, `expand` and `fields` as `get_list`.
        Pass `batch=100` when `fields` names a rollup or a formula column: the
        API caps that page at 100 rows.

            customers.get_full_list(filter='Status = "Active"', sort="title")

        Rows inserted while this is paging may be missed or seen twice, the
        same as any paged read.
        """
        batch = max(1, min(int(batch), PER_PAGE_MAX))
        options = _list_options(options, "get_full_list")
        rows = []
        page = 1
        while True:
            answer = self.get_list(page, batch, **options)
            items = list((answer or {}).get("items") or [])
            rows.extend(items)
            total_pages = int((answer or {}).get("totalPages") or 0)
            if not items or page >= total_pages:
                return rows
            page += 1

    def get_first_list_item(self, filter, **options):
        """The first row the filter matches, or `TruetaskError(404)`.

        Takes the same `sort`, `expand` and `fields` as `get_list`; `sort`
        decides which row is first, and without one it is the oldest.

            customers.get_first_list_item(tt.filter("Email = {:e}", e=address))
        """
        options = _list_options(options, "get_first_list_item", allow_filter=False)
        answer = self.get_list(1, 1, filter=filter, **options)
        items = list((answer or {}).get("items") or [])
        if not items:
            raise TruetaskError(
                404, "No record in this database matches that filter.", response={}
            )
        return items[0]

    def get_one(self, id, expand="", fields=""):
        """One row by its id, in the same shape the list returns.

        A row in the trash or the archive is not one: that answers 404.
        """
        params = _options(expand=expand, fields=fields)
        return self.client.request(
            "GET", self._records + "/" + _segment(id), params=params or None
        )

    def create(self, data):
        """Insert one row and answer it, re-read after the write.

        `data` is the row: `title` names the record and every other key is a
        column. Values are typed per column - a number as a number, a checkbox
        as a bool, a date as `"2026-01-31"`, a select as one of its choices, a
        duration as `"1h 30m"`, a people column as user ids, a relation as the
        target records' ids.

            customers.create({"title": "Acme", "Status": "Active", "Amount": 1200.5})
        """
        return self.client.request("POST", self._records, body=dict(data or {}))

    def update(self, id, data):
        """Change the columns `data` names and answer the row, re-read.

        A column it does not name is left alone. `None` clears a cell, and a
        relation column takes the list of target ids it should end up with, so
        `[]` unlinks it.

            customers.update(row["id"], {"Amount": 14, "Contact": []})
        """
        return self.client.request(
            "PATCH", self._records + "/" + _segment(id), body=dict(data or {})
        )

    def delete(self, id):
        """Move one row to the database's trash, where it can be restored.
        Answers None."""
        return self.client.request("DELETE", self._records + "/" + _segment(id))


# ── Writing a filter's values ──────────────────────────────────────────


def _literal(name, value):
    """One bound value as the filter grammar spells it."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _quoted(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _number(name, value)
    if isinstance(value, decimal.Decimal):
        return _number(name, value)
    # Before `date`: a datetime IS a date, and carries a time the other does
    # not.
    if isinstance(value, datetime.datetime):
        return _quoted(_stamp(value))
    if isinstance(value, datetime.date):
        return _quoted(value.isoformat())
    raise TypeError(
        "filter: {{:{}}} got {}; a filter value is text, a number, True, False, "
        "None, a date or a datetime. For a set of values write "
        "`(c = {{:a}} || c = {{:b}})`.".format(name, type(value).__name__)
    )


def _quoted(text):
    """A string as a double-quoted literal. The server reads `\\X` as `X` for
    any X, so a backslash and a quote are the whole escape."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _number(name, value):
    """A number written out in full. The server's grammar is
    `-?\\d+(\\.\\d+)?`, with no exponent, so `1e-05` would be a 400."""
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(
            "filter: {{:{}}} got {}; a filter has no word for it.".format(name, value)
        )
    text = repr(value) if isinstance(value, float) else str(value)
    if "e" in text or "E" in text:
        text = format(decimal.Decimal(text), "f")
    return text


def _stamp(value):
    """A datetime the way Truetask stores one: UTC, and a SPACE before the
    time. `isoformat`'s `T` sorts after a space, so a `>` written with one
    would answer the wrong rows."""
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M:%S")


# ── Odds and ends ──────────────────────────────────────────────────────


def _segment(id):
    """A record id as one path segment.

    An empty one is refused rather than sent: `records/` with nothing after it
    is the LIST route, so `get_one("")` would answer a page of rows.
    """
    text = str(id or "").strip()
    if not text:
        raise TruetaskError(0, "No record id: pass the row's `id`.")
    return urllib.parse.quote(text, safe="")


def _options(**given):
    """The query parameters that were actually given: an empty one is left out
    so the API uses its own default."""
    return {key: value for key, value in given.items() if str(value or "") != ""}


def _list_options(options, method, allow_filter=True):
    """`filter`, `sort`, `expand` and `fields` out of **options, with anything
    else refused by name rather than ignored."""
    allowed = _LIST_OPTIONS if allow_filter else _LIST_OPTIONS[1:]
    unknown = sorted(set(options) - set(allowed))
    if unknown:
        raise TypeError(
            "{}() got an unexpected keyword argument '{}'. It takes {}.".format(
                method, unknown[0], ", ".join(allowed)
            )
        )
    return {key: options[key] for key in allowed if key in options}


def _refused(err):
    """An HTTPError as a TruetaskError carrying what the API said."""
    try:
        body = json.loads(err.read().decode("utf-8"))
    except Exception:
        body = None
    detail = None
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("error") or body.get("message")
    if isinstance(detail, list):
        # FastAPI's own request validation answers a list of problems.
        detail = "; ".join(
            str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail
        )
    message = str(detail) if detail else "The API answered {}".format(err.code)
    return TruetaskError(
        err.code, message, response=body, retry_after=_retry_after(err)
    )


def _retry_after(err):
    """The seconds a `Retry-After` asked for, which a 429 carries."""
    try:
        raw = (err.headers or {}).get("Retry-After")
    except Exception:
        raw = None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None
