# Releasing

A tag publishes this package.
No token is stored anywhere: PyPI authenticates the workflow itself through OIDC, which it calls trusted publishing.

## Once, before the first release

1. Create `truetask-io/truetask-client-python` on GitHub, empty.
   Decline the README and the license it offers, since this history already has them.
2. Add a deploy key with write access, then push `main`.
3. On PyPI, open Your projects -> Publishing and add a **pending publisher**.
   PyPI takes one for a name that does not exist yet, which is how the first release works without a token:
   - PyPI project name: `truetask-db`
   - Owner: `truetask-io`
   - Repository: `truetask-client-python`
   - Workflow: `publish.yml`
   - Environment: `pypi`
4. GitHub creates the `pypi` environment the first time the workflow names it.
   Create it ahead in Settings -> Environments only to add yourself as a required reviewer.

Worth doing at the same time: register `truetask` on PyPI as a placeholder that points here.
It is the name people guess first, and an empty name invites a squatter.

## Each release

1. Bump `__version__` in `truetask_db/__init__.py`.
   `pyproject.toml` reads the version from there, so it is the only place.
2. Move the changelog's top section from `unreleased` to the version.
3. Commit, then tag and push:

```bash
git tag v0.1.0
git push origin main v0.1.0
```

The workflow refuses a tag that disagrees with `__version__`, runs the tests, builds, and uploads.

A version goes out once.
PyPI never lets a version number be reused, even after a delete, so a mistake costs a patch release rather than a re-upload.
