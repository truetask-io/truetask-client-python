"""The client's own tests: `python3 -m unittest discover -s tests`.

They run it against a small HTTP server in this process, which records what it
was sent and answers what a test told it to, so every method, the paging, the
filter binding and the error mapping are checked without a Truetask anywhere
near. The live check against a real stack is `examples/live_check.py`.
"""

import datetime
import json
import os
import sys
import threading
import unittest
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(HERE)
sys.path.insert(0, PACKAGE_ROOT)

import truetask_db  # noqa: E402
from truetask_db import Database, Truetask, TruetaskError  # noqa: E402

DB = "8cv0q1w2e3r4t5y"
ROW = {"id": "r1", "key": "CUST-1", "title": "Acme", "Amount": 1200.5}


def page(items, page_no=1, per_page=30, total=None, total_pages=1):
    return {
        "page": page_no,
        "perPage": per_page,
        "totalItems": total if total is not None else len(items),
        "totalPages": total_pages,
        "items": items,
    }


class Api(BaseHTTPRequestHandler):
    """Answers whatever the running test queued, and records every call."""

    seen = []
    answers = deque()

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        Api.seen.append(
            {
                "method": self.command,
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "agent": self.headers.get("User-Agent"),
                "body": body,
            }
        )
        status, answer, headers = Api.answers.popleft() if Api.answers else (200, {}, {})
        if isinstance(answer, bytes):
            raw = answer  # whatever the test wants on the wire, JSON or not
        else:
            raw = b"" if answer is None else json.dumps(answer).encode("utf-8")
        headers = dict(headers or {})
        short_by = int(headers.pop("X-Cut-Short", 0))
        self.send_response(status)
        if raw:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw) + short_by))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    do_GET = do_POST = do_PATCH = do_DELETE = _handle

    def log_message(self, *args):
        pass


class Stubbed(unittest.TestCase):
    """A test with the stub server running and a client pointed at it."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:{}".format(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Api.seen = []
        Api.answers = deque()
        self.tt = Truetask(self.url, "ttdb_key", timeout=5)
        self.rows = self.tt.database(DB)

    def will_answer(self, status, body, headers=None):
        Api.answers.append((status, body, headers or {}))

    @property
    def call(self):
        return Api.seen[-1]

    def query(self, index=-1):
        from urllib.parse import parse_qsl, urlsplit

        return dict(parse_qsl(urlsplit(Api.seen[index]["path"]).query))

    def path(self, index=-1):
        from urllib.parse import urlsplit

        return urlsplit(Api.seen[index]["path"]).path


class TheCredential(Stubbed):
    def test_every_call_carries_the_credential_and_names_itself(self):
        # urllib's own "Python-urllib/3.x" is refused by Cloudflare (1010).
        self.will_answer(200, {"id": DB, "fields": []})
        self.rows.schema()
        self.assertEqual(self.call["auth"], "Bearer ttdb_key")
        self.assertRegex(
            self.call["agent"], r"^truetask-db-python/\d+\.\d+\.\d+ Python/3\."
        )

    def test_it_falls_back_to_the_environment(self):
        saved = {k: os.environ.pop(k, None) for k in ("TRUETASK_URL", "TRUETASK_API_TOKEN")}
        try:
            os.environ["TRUETASK_URL"] = self.url + "/"
            os.environ["TRUETASK_API_TOKEN"] = "tt_personal"
            client = Truetask()
            self.assertEqual(client.url, self.url)
            self.assertEqual(client.credential, "tt_personal")
        finally:
            for key, value in saved.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value

    def test_it_says_what_is_missing(self):
        saved = {k: os.environ.pop(k, None) for k in ("TRUETASK_URL", "TRUETASK_API_TOKEN")}
        try:
            with self.assertRaisesRegex(TruetaskError, "TRUETASK_URL"):
                Truetask()
            with self.assertRaisesRegex(TruetaskError, "TRUETASK_API_TOKEN"):
                Truetask(self.url)
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_the_package_is_importable_both_ways(self):
        from truetask_db.db import Truetask as FromModule

        self.assertIs(FromModule, Truetask)
        self.assertRegex(truetask_db.__version__, r"^\d+\.\d+\.\d+$")


class Reading(Stubbed):
    def test_schema_asks_the_database_itself(self):
        answer = {"id": DB, "name": "Customers", "key_prefix": "CUST", "fields": []}
        self.will_answer(200, answer)
        self.assertEqual(self.rows.schema(), answer)
        self.assertEqual(self.call["method"], "GET")
        self.assertEqual(self.path(), "/v1/db/" + DB)

    def test_get_list_passes_every_option(self):
        self.will_answer(200, page([ROW]))
        answer = self.rows.get_list(
            2, 100, filter='Status = "Active"', sort="-Amount", expand="Contact", fields="title,Amount"
        )
        self.assertEqual(answer["items"], [ROW])
        self.assertEqual(self.path(), "/v1/db/{}/records".format(DB))
        self.assertEqual(
            self.query(),
            {
                "page": "2",
                "per_page": "100",
                "filter": 'Status = "Active"',
                "sort": "-Amount",
                "expand": "Contact",
                "fields": "title,Amount",
            },
        )

    def test_get_list_leaves_an_empty_option_out(self):
        self.will_answer(200, page([]))
        self.rows.get_list()
        self.assertEqual(self.query(), {"page": "1", "per_page": "30"})

    def test_get_one_asks_for_the_record(self):
        self.will_answer(200, ROW)
        self.assertEqual(self.rows.get_one("r1", expand="Contact"), ROW)
        self.assertEqual(self.path(), "/v1/db/{}/records/r1".format(DB))
        self.assertEqual(self.query(), {"expand": "Contact"})

    def test_an_empty_id_is_refused_rather_than_sent(self):
        # `records/` with nothing after it is the LIST route, so an empty id
        # would answer a page of rows instead of saying nothing was asked for.
        for method, args in (
            ("get_one", ("",)),
            ("update", (None, {"Amount": 1})),
            ("delete", ("   ",)),
        ):
            with self.assertRaisesRegex(TruetaskError, "No record id"):
                getattr(self.rows, method)(*args)
        self.assertEqual(Api.seen, [])

    def test_an_id_stays_one_path_segment(self):
        self.will_answer(200, ROW)
        self.rows.get_one("a/b?c")
        self.assertEqual(self.path(), "/v1/db/{}/records/a%2Fb%3Fc".format(DB))

    def test_get_full_list_reads_every_page(self):
        first = [dict(ROW, id="r{}".format(n)) for n in range(2)]
        second = [dict(ROW, id="r{}".format(n)) for n in range(2, 4)]
        last = [dict(ROW, id="r4")]
        for n, items in enumerate((first, second, last), start=1):
            self.will_answer(200, page(items, page_no=n, per_page=2, total=5, total_pages=3))
        got = self.rows.get_full_list(batch=2, filter="Amount > 0", sort="title")
        self.assertEqual([r["id"] for r in got], ["r0", "r1", "r2", "r3", "r4"])
        self.assertEqual([self.query(i)["page"] for i in (0, 1, 2)], ["1", "2", "3"])
        self.assertEqual(self.query(0)["per_page"], "2")
        self.assertEqual(self.query(2)["sort"], "title")

    def test_get_full_list_stops_on_an_empty_page(self):
        self.will_answer(200, page([], page_no=1, total=0, total_pages=9))
        self.assertEqual(self.rows.get_full_list(), [])
        self.assertEqual(len(Api.seen), 1)

    def test_get_full_list_asks_for_no_more_than_the_api_answers(self):
        self.will_answer(200, page([ROW], total_pages=1))
        self.rows.get_full_list(batch=100000)
        self.assertEqual(self.query()["per_page"], str(truetask_db.PER_PAGE_MAX))

    def test_the_computed_cap_is_on_the_surface(self):
        # The page a rollup column fits in, so a caller can name it.
        self.will_answer(200, page([ROW], total_pages=1))
        self.rows.get_full_list(batch=truetask_db.COMPUTED_PER_PAGE_MAX, fields="Total")
        self.assertEqual(self.query()["per_page"], "100")
        self.assertEqual(self.query()["fields"], "Total")

    def test_get_full_list_refuses_an_option_it_does_not_have(self):
        with self.assertRaisesRegex(TypeError, "per_page"):
            self.rows.get_full_list(per_page=10)
        self.assertEqual(Api.seen, [])

    def test_get_first_list_item_asks_for_one_row(self):
        self.will_answer(200, page([ROW], per_page=1, total=7))
        self.assertEqual(self.rows.get_first_list_item('Email = "a@b.c"', sort="-created"), ROW)
        self.assertEqual(
            self.query(),
            {"page": "1", "per_page": "1", "filter": 'Email = "a@b.c"', "sort": "-created"},
        )

    def test_get_first_list_item_says_404_when_nothing_matches(self):
        self.will_answer(200, page([], per_page=1, total=0))
        with self.assertRaises(TruetaskError) as caught:
            self.rows.get_first_list_item('Email = "nobody@example.com"')
        self.assertEqual(caught.exception.status, 404)
        self.assertIn("matches that filter", caught.exception.message)

    def test_get_first_list_item_keeps_its_own_filter(self):
        with self.assertRaisesRegex(TypeError, "filter"):
            self.rows.get_first_list_item("a = 1", filter="b = 2")


class Writing(Stubbed):
    def test_create_sends_the_row(self):
        self.will_answer(201, ROW)
        made = self.rows.create({"title": "Acme", "Amount": 1200.5})
        self.assertEqual(made, ROW)
        self.assertEqual(self.call["method"], "POST")
        self.assertEqual(self.path(), "/v1/db/{}/records".format(DB))
        self.assertEqual(self.call["body"], {"title": "Acme", "Amount": 1200.5})

    def test_update_sends_only_what_changed(self):
        self.will_answer(200, dict(ROW, Amount=14))
        changed = self.rows.update("r1", {"Amount": 14, "Contact": []})
        self.assertEqual(changed["Amount"], 14)
        self.assertEqual(self.call["method"], "PATCH")
        self.assertEqual(self.path(), "/v1/db/{}/records/r1".format(DB))
        self.assertEqual(self.call["body"], {"Amount": 14, "Contact": []})

    def test_delete_answers_nothing(self):
        self.will_answer(204, None)
        self.assertIsNone(self.rows.delete("r1"))
        self.assertEqual(self.call["method"], "DELETE")
        self.assertEqual(self.path(), "/v1/db/{}/records/r1".format(DB))


class Visitors(Stubbed):
    """The whole of DEVE-1014 on this side: one call that answers a token, and
    then nothing else changes. A client holding a visitor's token sends it
    where the key went, so every method above is untouched."""

    MINTED = {
        "id": "vis1",
        "token": "ttv_abc",
        "external_id": "user_8842",
        "expires": "2027-02-08 11:20:04.000Z",
        "created": "2026-11-10 11:20:04.000Z",
    }

    def test_minting_posts_to_the_database_s_visitors(self):
        self.will_answer(201, self.MINTED)
        minted = self.rows.visitors.create(external_id="user_8842")
        self.assertEqual(minted, self.MINTED)
        self.assertEqual(self.call["method"], "POST")
        self.assertEqual(self.path(), "/v1/db/{}/visitors".format(DB))
        self.assertEqual(self.call["body"], {"external_id": "user_8842"})
        self.assertEqual(self.call["auth"], "Bearer ttdb_key")

    def test_an_anonymous_visitor_still_sends_the_key_the_server_reads(self):
        # Most of them are anonymous: the field goes out empty rather than
        # being left off, so the server reads one shape of body.
        self.will_answer(201, dict(self.MINTED, external_id=""))
        self.rows.visitors.create()
        self.assertEqual(self.call["body"], {"external_id": ""})

    def test_the_token_is_a_plain_credential_from_then_on(self):
        # The swap, which is all a site has to do: the same client with the
        # visitor's token on it, calling the same methods.
        visitor = Truetask(self.url, self.MINTED["token"], timeout=5).database(DB)
        self.will_answer(200, {"items": [dict(ROW, mine=True)], "totalPages": 1})
        page = visitor.get_list(1, 30, filter="mine = true")
        self.assertEqual(page["items"][0]["mine"], True)
        self.assertEqual(self.call["auth"], "Bearer ttv_abc")
        self.assertEqual(self.query()["filter"], "mine = true")

    def test_a_key_without_the_right_is_the_api_s_refusal_to_read(self):
        self.will_answer(403, {"detail": "This credential cannot visitors records in this database."})
        with self.assertRaises(TruetaskError) as refused:
            self.rows.visitors.create()
        self.assertEqual(refused.exception.status, 403)
        self.assertIn("visitors", refused.exception.message)


class Refusals(Stubbed):
    def test_a_refusal_carries_the_apis_reason_and_its_body(self):
        body = {"detail": "This credential cannot insert records in this database."}
        self.will_answer(403, body)
        with self.assertRaises(TruetaskError) as caught:
            self.rows.create({"title": "Acme"})
        error = caught.exception
        self.assertEqual(error.status, 403)
        self.assertEqual(error.message, body["detail"])
        self.assertEqual(error.response, body)
        self.assertIsNone(error.retry_after)
        self.assertEqual(str(error), body["detail"])

    def test_a_validation_list_is_joined(self):
        self.will_answer(
            422,
            {"detail": [{"loc": ["body", "title"], "msg": "field required"}, {"msg": "nope"}]},
        )
        with self.assertRaises(TruetaskError) as caught:
            self.rows.create({})
        self.assertEqual(caught.exception.message, "field required; nope")

    def test_a_structured_refusal_reads_as_its_message(self):
        body = {
            "detail": {
                "feature": "database_keys",
                "message": "Database API keys need a paid plan",
            }
        }
        self.will_answer(403, body)
        with self.assertRaises(TruetaskError) as caught:
            self.rows.get_list()
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(caught.exception.message, "Database API keys need a paid plan")
        self.assertEqual(caught.exception.response["detail"]["feature"], "database_keys")

    def test_a_missing_row_is_404(self):
        self.will_answer(404, {"detail": "Record not found"})
        with self.assertRaises(TruetaskError) as caught:
            self.rows.get_one("nope")
        self.assertEqual((caught.exception.status, caught.exception.message), (404, "Record not found"))

    def test_a_body_that_is_not_json_still_says_the_status(self):
        self.will_answer(502, None)
        with self.assertRaises(TruetaskError) as caught:
            self.rows.schema()
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(caught.exception.message, "The API answered 502")
        self.assertEqual(caught.exception.response, {})

    def test_over_the_limit_says_how_long_to_wait(self):
        self.will_answer(
            429, {"detail": "Too many requests"}, headers={"Retry-After": "12"}
        )
        with self.assertRaises(TruetaskError) as caught:
            self.rows.get_list()
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, 12)

    def test_an_answer_that_is_not_json_is_still_a_truetask_error(self):
        # Something in front of Truetask: a proxy's error page, a sign-in wall.
        self.will_answer(200, b"<html>Just a moment...</html>")
        with self.assertRaises(TruetaskError) as caught:
            self.rows.get_list()
        self.assertEqual(caught.exception.status, 200)
        self.assertIn("but not JSON", caught.exception.message)
        self.assertIn("Just a moment", caught.exception.message)

    def test_a_truncated_answer_is_still_a_truetask_error(self):
        # http.client raises its own for this, and it is not an OSError.
        self.will_answer(200, {"id": DB}, headers={"X-Cut-Short": 64})
        with self.assertRaises(TruetaskError) as caught:
            self.rows.schema()
        self.assertEqual(caught.exception.status, 0)
        self.assertIn("Could not reach", caught.exception.message)

    def test_a_server_it_cannot_reach_is_status_zero(self):
        nowhere = Truetask("http://127.0.0.1:1", "ttdb_key", timeout=2)
        with self.assertRaises(TruetaskError) as caught:
            nowhere.database(DB).schema()
        self.assertEqual(caught.exception.status, 0)
        self.assertIn("Could not reach", caught.exception.message)


class Filters(unittest.TestCase):
    """`Truetask.filter` binds a value as the server's grammar spells it:
    `db_filter.py` reads `\\X` as `X` inside a quoted literal, and its number
    is `-?\\d+(\\.\\d+)?` with no exponent."""

    def bind(self, expr, **params):
        return Truetask.filter(expr, **params)

    def test_text_is_quoted(self):
        self.assertEqual(self.bind("a = {:v}", v="Active"), 'a = "Active"')

    def test_an_apostrophe_needs_no_escape_in_a_double_quoted_literal(self):
        self.assertEqual(self.bind("a = {:v}", v="O'Neil"), 'a = "O\'Neil"')

    def test_a_quote_cannot_end_the_literal(self):
        self.assertEqual(self.bind("a = {:v}", v='say "hi"'), 'a = "say \\"hi\\""')

    def test_a_backslash_is_doubled(self):
        self.assertEqual(self.bind("a = {:v}", v="a\\b"), 'a = "a\\\\b"')

    def test_a_trailing_backslash_cannot_eat_the_closing_quote(self):
        # The hazard: "c:\" would leave the literal unclosed and the rest of
        # the filter inside it.
        self.assertEqual(self.bind("a = {:v}", v="c:\\"), 'a = "c:\\\\"')

    def test_an_injection_attempt_stays_one_value(self):
        self.assertEqual(
            self.bind("a = {:v}", v='x" || id != ""'),
            'a = "x\\" || id != \\"\\""',
        )

    def test_numbers(self):
        self.assertEqual(self.bind("a = {:v}", v=10), "a = 10")
        self.assertEqual(self.bind("a = {:v}", v=-3), "a = -3")
        self.assertEqual(self.bind("a = {:v}", v=2.5), "a = 2.5")

    def test_a_small_number_is_written_out_not_in_exponent_form(self):
        self.assertEqual(self.bind("a > {:v}", v=1e-05), "a > 0.00001")
        self.assertEqual(self.bind("a < {:v}", v=1e22), "a < 10000000000000000000000")

    def test_a_decimal_is_a_number_too(self):
        import decimal

        self.assertEqual(self.bind("a = {:v}", v=decimal.Decimal("12.30")), "a = 12.30")

    def test_a_number_a_filter_has_no_word_for(self):
        with self.assertRaisesRegex(ValueError, "inf"):
            self.bind("a = {:v}", v=float("inf"))
        with self.assertRaisesRegex(ValueError, "nan"):
            self.bind("a = {:v}", v=float("nan"))

    def test_booleans_and_nothing(self):
        self.assertEqual(self.bind("a = {:v}", v=True), "a = true")
        self.assertEqual(self.bind("a = {:v}", v=False), "a = false")
        self.assertEqual(self.bind("a = {:v}", v=None), "a = null")

    def test_a_date_is_the_shape_a_date_column_stores(self):
        self.assertEqual(
            self.bind("Due = {:v}", v=datetime.date(2026, 1, 31)), 'Due = "2026-01-31"'
        )

    def test_a_datetime_is_the_shape_a_stamp_stores(self):
        # A space, not isoformat's `T`: `T` sorts after a space, so a `>`
        # written with one would answer the wrong rows.
        self.assertEqual(
            self.bind("created > {:v}", v=datetime.datetime(2026, 11, 10, 13, 24, 49)),
            'created > "2026-11-10 13:24:49"',
        )

    def test_a_datetime_with_a_zone_is_moved_to_utc(self):
        madrid = datetime.timezone(datetime.timedelta(hours=2))
        when = datetime.datetime(2026, 11, 10, 15, 24, 49, tzinfo=madrid)
        self.assertEqual(self.bind("created > {:v}", v=when), 'created > "2026-11-10 13:24:49"')

    def test_every_placeholder_is_bound(self):
        self.assertEqual(
            self.bind(
                "(Status = {:s} || Status = {:s2}) && Amount > {:n}",
                s="Active",
                s2="Paused",
                n=10,
            ),
            '(Status = "Active" || Status = "Paused") && Amount > 10',
        )

    def test_the_same_placeholder_twice(self):
        self.assertEqual(
            self.bind("a = {:v} || b = {:v}", v="x"), 'a = "x" || b = "x"'
        )

    def test_a_filter_with_nothing_to_bind_is_itself(self):
        self.assertEqual(self.bind('Status = "Active"'), 'Status = "Active"')

    def test_a_placeholder_with_nothing_passed_for_it(self):
        with self.assertRaises(KeyError) as caught:
            self.bind("a = {:missing}")
        self.assertIn("{:missing}", str(caught.exception))

    def test_a_spare_value_is_ignored_so_one_dict_serves_several_filters(self):
        values = {"a": 1, "b": 2}
        self.assertEqual(self.bind("x = {:a}", **values), "x = 1")

    def test_a_value_a_filter_cannot_say(self):
        with self.assertRaisesRegex(TypeError, r"\{:v\} got list"):
            self.bind("a = {:v}", v=["x", "y"])
        with self.assertRaisesRegex(TypeError, r"\{:v\} got dict"):
            self.bind("a = {:v}", v={"x": 1})

    def test_text_that_looks_like_a_placeholder_is_left_alone(self):
        self.assertEqual(self.bind("a = {:v}", v="{:v}"), 'a = "{:v}"')

    def test_it_is_on_an_instance_too(self):
        self.assertEqual(
            Truetask("http://example.test", "k").filter("a = {:v}", v=1), "a = 1"
        )


class ThePythonItRunsIn(unittest.TestCase):
    def test_it_says_nothing_newer_than_python_37(self):
        # The Python a DCC ships is the oldest this has to run in, and `3.7`
        # in pyproject.toml is a promise only this keeps.
        import ast
        import io

        if sys.version_info < (3, 8):  # pragma: no cover - the check needs 3.8
            self.skipTest("feature_version arrived in 3.8")
        for name in ("truetask_db/__init__.py", "truetask_db/db.py", "examples/every_row.py"):
            path = os.path.join(PACKAGE_ROOT, name)
            with io.open(path, encoding="utf-8") as handle:
                ast.parse(handle.read(), filename=path, feature_version=(3, 7))


class TheDatabaseHandle(unittest.TestCase):
    def test_an_empty_database_id_is_refused(self):
        tt = Truetask("http://example.test", "k")
        for bad in ("", None, "   "):
            with self.assertRaisesRegex(TruetaskError, "No database id"):
                tt.database(bad)

    def test_it_says_which_database_it_is(self):
        rows = Truetask("http://example.test", "k").database(DB)
        self.assertIsInstance(rows, Database)
        self.assertEqual(rows.id, DB)
        self.assertIn(DB, repr(rows))


if __name__ == "__main__":
    unittest.main()
