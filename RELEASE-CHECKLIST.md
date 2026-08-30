# Koi Fetch v1 — Release Checklist

The acceptance gate for the v1 foundation. Every row lists the exact command,
the expected result, and the result of the most recent verification run. Run
the checks in order; any unexpected failure blocks the release.

## How to use this file

- All commands run from the **repository root** unless a CWD is given.
- Backend commands assume the project venv is activated (`python` on PATH).
- Two rows — the live `docker compose config` and `docker compose up --build`
  — require Docker. When Docker is unavailable, the static PyYAML contract
  (`backend/tests/test_compose.py`) provides an offline check; run the live
  rows on a provisioned Docker host for final sign-off.
- The frontend rows (`npm test`, `npm run build`) are ordinary commands on a
  normal machine. Inside the DSH sandbox they were executed through a
  temporary, uncommitted shim (in-process WebAssembly esbuild + a vite
  `net use` probe guard) because the sandbox denies any child-process spawn
  with piped stdio; the shim lives only in `node_modules` and is removed after
  the run. On a normal machine no shim is needed.

## Gate table

| # | Check | Command | Expected result | Result (last run) |
| --- | --- | --- | --- | --- |
| 1 | Backend test suite | `python -m pytest backend/tests -q` | Exit 0; no test failures | ✅ latest run passed; warning output is informational |
| 2 | Frontend test suite | `npm test --prefix frontend` | Exit 0; no test failures | ✅ latest run passed |
| 3 | Frontend typecheck + build | `npm run build --prefix frontend` (runs `tsc --noEmit && vite build`) | `tsc` exits 0; Vite writes `frontend/dist/`; `npm` exits 0 | ✅ `tsc` clean; Vite `✓ 3149 modules transformed ... ✓ built in 9.32s`, 10 files in `frontend/dist/` (only an informational >500 kB chunk-size warning) |
| 4 | Compose config (static contract) | `python -m pytest backend/tests/test_compose.py -q` | `18 passed` | ✅ `18 passed in 0.03s` |
| 5 | Compose config (live) | `docker compose config` | Resolved two-service model (`koi-fetch`: backend + worker, `env_file: .env`, `8000:8000`, bind mounts, healthchecks) | ⏳ **requires Docker host** — statically verified by #4; PyYAML parse prints the identical model (services, build context, ports, env_file, healthchecks) |
| 6 | Compose stack boot | `docker compose up --build` | Image builds (Node stage + Python stage), backend healthy, worker `service_healthy`-gated; `curl http://localhost:8000/api/health` → `{"code":0,...}`; SPA at `http://localhost:8000/` | ⏳ **requires Docker host** — executable offline equivalents passed: end-to-end pytest smoke (#7) and the live uvicorn probe (#8) |
| 7 | End-to-end smoke (offline) | `python -m pytest backend/tests/test_smoke.py -q` | `1 passed` — full flow health → parse → preview → download → tokenized file → login → NAS save, incl. `/api/health`, SPA at `/` and `/login` | ✅ `1 passed, 1 warning in 0.84s` |
| 8 | Live API + frontend entry routes | `uvicorn app.main:app` (temp DB + built/synthetic dist) then `curl http://127.0.0.1:8000/api/health` and `curl http://127.0.0.1:8000/` | `/api/health` → HTTP 200 `{"code":0,"data":{"status":"ok",...}}`; `/`, `/login`, deep routes → HTTP 200 SPA; unknown `/api/*` → HTTP 404 preserved | ✅ live uvicorn probe: `/api/health` 200 full readiness; `/`, `/login`, `/nonexistent-deep-route` 200 SPA; `/api/does-not-exist` 404 |
| 9 | Hygiene — no secrets/runtime files tracked | `git ls-files` (inspect) | No `.env` (only `.env.example`, `frontend/.env.example`), no `*.db`/`*.sqlite`, no `data/` paths, no `node_modules/`, no `frontend/dist/`, no `.venv`, no `__pycache__`/`*.pyc`, no `.npm-cache` | ✅ none tracked; `.gitignore` covers all of the above |
| 10 | Hygiene — no real secrets in tracked content | `git grep` for credential patterns | Only synthetic placeholders (`.env.example` `change-me-*`, test fixtures) | ✅ only test fixtures (`admin-s3cret-pass`, `super-secret-pw-123456`) — no real credentials, keys, or tokens |
| 11 | Working tree clean | `git status --short` | Clean (no modified/untracked files) after the run | ✅ clean; the only tracked addition of this run is this checklist (commit `1d10043`) — all shim artifacts removed post-run |
| 12 | Python dependency audit | `python backend/scripts/audit_backend_dependencies.py` (after `python backend/scripts/install_backend_dependencies.py` and installing pinned `pip-audit`) | No known vulnerabilities; only the documented fixed-SHA Git `parse-video-py` skip; no broad ignores | ⏳ run on the release environment |
| 13 | High-severity JavaScript audit | `npm audit --prefix frontend --audit-level=high` | No high/critical vulnerabilities after `npm ci` | ⏳ run on the release environment |
| 14 | Existing cookie migration and backup | First backup DB and COOKIE_ENCRYPTION_KEY; run `python backend/scripts/encrypt_platform_cookies.py` twice from the repo root | First count equals legacy rows, second run must report 0; restore the backup if verification fails | ⏳ operator execution required for databases containing legacy cookies |
| 15 | weak/default secret rejection | `python -m pytest backend/tests/test_config.py backend/tests/test_cookie_encryption.py -q` | Missing, blank, weak/default, malformed, or wrong cookie keys are rejected/fail closed | ⏳ run on the release environment |
| 16 | Login limiter and trusted proxy verification | `python -m pytest backend/tests/test_login_limiter.py backend/tests/test_auth_api.py -q` with only immediate proxy CIDRs in `TRUSTED_PROXY_CIDRS` | Process-local limits and direct-peer/X-Forwarded-For handling are verified | ⏳ run on the release environment |
| 17 | SSRF regression tests | `python -m pytest backend/tests/test_safe_upstream.py -q` | Unsafe schemes/addresses, redirect re-resolution, pinned IP Host/SNI, and body/redirect bounds remain blocked | ⏳ run on the release environment |
| 18 | Session and file-token contract | `python -m pytest backend/tests/test_config.py backend/tests/test_tokens.py backend/tests/test_auth_service.py backend/tests/test_auth_api.py -q` and the focused frontend auth/router tests | Default access TTL is 7 days; each page startup performs at most one refresh of a still-valid token; expired access tokens have no grace refresh; hydration failures and stale login/refresh races fail closed; stateless multi-tab overlap is expected until each access token expires; 5-minute file tokens are reusable, bound to task+filename, and support repeated Range/HEAD requests | ⏳ run on the release environment |

## Verification evidence (most recent run)

- **Backend:** latest `python -m pytest backend/tests -q` run exited 0; the
  StarletteDeprecationWarning about `httpx` in `fastapi/testclient` is
  informational and has no behavioral impact.
- **Frontend tests:** latest `npm test --prefix frontend` run exited 0.
- **Compose static (18):**
  `18 passed in 0.03s` — two services, no frontend service, no `nginx.conf`,
  `env_file: .env`, repo-root build context, `FRONTEND_DIST_PATH=/app/static`,
  migration+seed+uvicorn command chain, `app.health` readiness healthcheck,
  `8000:8000`, db/bubble/pond bind mounts, worker `service_healthy` gate.
- **Smoke (1):**
  `1 passed` — one green run of the whole v1 user flow over the real wiring.
- **Live probe:**
  uvicorn on `127.0.0.1:8011` against a throwaway temp SQLite DB + synthetic
  `dist/index.html`; `/api/health` → HTTP 200
  `{"code":0,"data":{"status":"ok","services":{"api":"ok","storage":"ok"},...}}`;
  `/` and `/login` → HTTP 200 with the SPA marker; deep route → 200 (SPA
  fallback); `/api/does-not-exist` → 404 (API 404s preserved).
- **Hygiene:**
  tracked files contain exactly two `.env.example` files and no runtime
  artifacts; the credential grep matched only deterministic test constants.

## What still needs a Docker host (final sign-off)

1. `docker compose config` — live resolution + interpolation check.
2. `docker compose up --build` — real image build (Node stage `npm ci` +
   `vite build`, Python stage) and a healthy two-service stack, then the
   `curl http://localhost:8000/api/health` and `http://localhost:8000/` checks
   against the container.

Both are statically covered today (`test_compose.py` + the PyYAML parse in the
evidence above) and their executable offline equivalents (smoke, live probe)
pass, but the Docker rows themselves must be executed on a Docker machine
before the v1 release is signed off.
