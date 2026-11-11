# Changelog

## 0.1.0 - 2026-11-11

### Added

- A database reads and writes with the methods the PocketBase SDK uses
- Parameters bind into a filter, so a value from a form cannot rewrite the query
- Paging that walks itself, for a whole table in one call
- A database key, a personal token or a session JWT, all as the same credential
- A visitor identity whose own rows are the only ones it can change
- One error type carrying the status, the server's message and a retry wait
- A structured refusal, such as a plan's, reads as its message
- Python 3.7 and later, standard library only
