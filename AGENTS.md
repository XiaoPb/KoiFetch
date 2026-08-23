# Repository Guidelines

## Project Structure & Module Organization

This repository is currently a documentation-first scaffold. `docs/prd/KoiFetch.md` contains the product overview and `docs/prd/KoiFetch-PRD.md` defines the planned API, data model, UI, and deployment architecture. Application code should be organized into separate `backend/` and `frontend/` trees: place FastAPI services, integrations, and persistence code under `backend/app/`, React components and pages under `frontend/src/`, and automated tests in `backend/tests/` and `frontend/src/**/*.test.*`. Keep runtime data out of Git (for example, `data/bubble/`, `data/pond/`, and `data/db/`).

## Documentation Version Control

Keep product requirements and local planning artifacts under `docs/prd/` and `docs/superpowers/`; both directories must remain ignored and untracked. Do not force-add files from either directory.

## Build, Test, and Development Commands

No build or test scripts exist yet. When implementation begins, document and preserve these project-standard entry points:

- `python -m pytest backend/tests` — run backend tests.
- `npm test --prefix frontend` — run frontend tests.
- `npm run dev --prefix frontend` — start the React development server.
- `uvicorn app.main:app --reload --app-dir backend` — run the FastAPI service locally.
- `docker compose up --build` — build and start the complete local stack.

## Coding Style & Naming Conventions

Use 4-space indentation and PEP 8 naming in Python (`snake_case` functions/modules, `PascalCase` classes). Use TypeScript/React with 2-space indentation, `PascalCase` components, and `camelCase` variables. Keep API schemas explicit and colocate feature-specific tests with their module area. Add formatter/linter configuration (such as Ruff/Black and ESLint/Prettier) before enforcing it in CI.

## Testing Guidelines

Test parsing, download state transitions, token expiry, storage boundaries, and permission checks. Name Python tests `test_<behavior>.py` and frontend tests `<Component>.test.tsx`. Include regression coverage for every bug fix; no coverage threshold is established yet, so report meaningful gaps in pull requests.

## Commit & Pull Request Guidelines

There is no commit history yet, so use imperative, scoped messages such as `feat(api): add parse endpoint` or `fix(storage): clean expired bubble files`. Pull requests should explain the behavior change, link the relevant issue or PRD section, list validation commands, and include screenshots or API examples for user-facing changes.

## Security & Configuration

Never commit secrets, downloaded media, database files, or NAS paths. Configure `ADMIN_PASSWORD` and `SECRET_KEY` through environment variables; use safe local values in `.env.example`, not real credentials.
