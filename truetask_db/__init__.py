"""A Truetask database as rows, from Python.

    from truetask_db import Truetask

    tt = Truetask("https://tasks.example.com", "ttdb_...")
    customers = tt.database("8cv0q1w2e3r4t5y")
    page = customers.get_list(1, 30, filter='Status = "Active"', sort="-Amount")

The whole guide is the README. It needs nothing outside the standard library
and runs on Python 3.7 and later.
"""

# Before the import below: the client names it in its User-Agent.
__version__ = "0.1.0"

from .db import (  # noqa: E402
    COMPUTED_PER_PAGE_MAX,
    PER_PAGE_MAX,
    Database,
    Truetask,
    TruetaskError,
)

__all__ = [
    "Truetask",
    "Database",
    "TruetaskError",
    "PER_PAGE_MAX",
    "COMPUTED_PER_PAGE_MAX",
    "__version__",
]
