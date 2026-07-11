# Contributing to Vigilus

Thanks for your interest in contributing! Vigilus is a solo-maintained project,
so the process below is intentionally lightweight — but following it keeps the
history clean and releases predictable.

## Local setup

Prerequisites: Python 3.11+, Node.js 18+.

### Backend

```bash
cd backend
pip install -e ".[dev]"
export VIGILUS_SECRET="$(openssl rand -hex 32)"   # required, including for tests

vigilus init     # create data dir + run migrations
vigilus start    # run the server (add --reload for dev autoreload)
```

### Frontend

```bash
cd frontend
npm install
npm run dev      # Vite dev server on :5173, proxies API to backend :8000
```

### Checks

Run these before opening a PR — CI runs the same steps:

```bash
# backend (from backend/)
ruff check .
black .
pytest

# frontend (from frontend/)
npm run lint
npm run build    # includes TypeScript type-checking
```

## Branches

- **`main`** — latest stable release. Only updated via release PRs from `dev`.
  Protected: no direct pushes, no force pushes.
- **`dev`** — default branch; ongoing work lands here.
- **Feature branches** — branch off `dev` for anything non-trivial, using the
  pattern `feat/<short-name>`, `fix/<issue-or-short-name>`, `docs/<short-name>`,
  or `chore/<short-name>`. Small changes can go straight to `dev` via PR.

## Pull requests

1. Branch off `dev` and make your changes.
2. Run the checks above.
3. Open a PR into `dev`. CI (lint, tests, type-check, build) must pass.
4. Update `CHANGELOG.md` under the **Unreleased** section if the change is
   user-visible.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <description>
```

Common types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`.

Examples:

- `feat(operators): add per-operator working directory confinement`
- `fix(jit): expire tokens on approval timeout`
- `docs: add Docker compose example to README`

Breaking changes get a `!` after the type (`feat!: ...`) and a description of
the break in the body.

## Release process

Releases are manual for now (no semantic-release/release-please). To cut a
release:

1. Open a PR merging `dev` → `main`.
2. In that PR (or just before it), bump the version — [semver](https://semver.org/) —
   in **both** manifests:
   - `backend/pyproject.toml` (`version = "X.Y.Z"`)
   - `frontend/package.json` (`"version": "X.Y.Z"`)
3. Update `CHANGELOG.md`: move the **Unreleased** items under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading, leaving an empty Unreleased section.
4. Merge the PR, then tag the merge commit on `main` and push the tag:

   ```bash
   git checkout main && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

   Pushing the tag triggers the Docker publish workflow (GHCR images tagged
   `X.Y.Z` and `X.Y`).
5. Create a GitHub Release from the tag, pasting the changelog notes for that
   version:

   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z" --notes "<changelog section>"
   ```

## Security

Vigilus drives LLM tools against real infrastructure. If you find a security
issue, please **do not** open a public issue — see the Security section of the
README and report it privately.
