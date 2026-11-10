"""Print a database's columns and then every row: the first thing to run with
a new credential.

    TRUETASK_URL=https://tasks.example.com \
    TRUETASK_API_TOKEN=ttdb_... \
    python3 examples/every_row.py 8cv0q1w2e3r4t5y
"""

import sys

from truetask_db import Truetask, TruetaskError


def main(database_id):
    rows = Truetask().database(database_id)
    schema = rows.schema()
    print("{}: {}".format(schema["name"], ", ".join(f["name"] for f in schema["fields"])))
    for row in rows.get_full_list(sort="title"):
        print(row["key"], row["title"])
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: every_row.py <database id>")
    try:
        sys.exit(main(sys.argv[1]))
    except TruetaskError as err:
        raise SystemExit("Truetask answered {}: {}".format(err.status, err.message))
