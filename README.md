# ACH Platform

A compliance-first ACH origination and receipt platform: a FastAPI engine that originates, receives, returns and settles entries under the Nacha Operating Rules, with sanctions, fraud and AML screening inside the payment flow, a double-entry ledger, three-way reconciliation and ITSM monitoring, plus a React operations console.

It implements the ACH Program Strategy end to end; see [docs/strategy-traceability.md](docs/strategy-traceability.md) for where each part of the strategy lives in the code, and what is simulated.

## What it does

- **Origination (ODFI / Third-Party Sender).** REST API and Nacha-file channel for PPD, CCD, CTX, WEB, TEL and IAT credits and debits. Every entry is validated against a dated rules catalog, scheduled into a FedACH window, screened, then queued or held.
- **Cut-offs, weekends and holidays.** Six FedACH windows with internal cut-offs 45 minutes before each Fed deadline. Settlement dates follow the Federal Reserve (K.8) holiday calendar: a Saturday holiday leaves the Fed open on Friday, a Sunday holiday closes it on Monday. Weekend and holiday effective dates move to the next banking day. Unscheduled closures go through maker-checker approval.
- **Controls in the path.** OFAC screening (with IAT party data), risk-based fraud monitoring for the 2026 Nacha rules (new payee, recently changed bank details, unvalidated WEB debits, duplicates, velocity, amount anomalies), AML typologies, originator exposure limits and prefunding checks.
- **Receipt (RDFI).** Automatic R02/R03/R16/R20/R29/R01 returns, debit blocks and filters, credit monitoring that holds funds on suspicious credits, funds availability by window (next-day credits by 9:00 a.m. local from Sept 18, 2026), unauthorized-debit claims with WSUD, and automatic C05 NOCs.
- **Exceptions.** Inbound returns with deadline and timeliness checks, NOC auto-application, Nacha-permissible reversals within 5 banking days, and return-rate monitoring against the 0.5% / 3% / 15% thresholds.
- **Money and evidence.** Integer cents, an append-only double-entry ledger, three-way reconciliation (Fed advice, ledger, core) per window, tokenized account numbers, signed webhooks and a full audit log.
- **Operations.** Scheduler that releases at cut-off and settles, P1/P2/P3 incidents (window at risk or missed, reconciliation breaks, rejected files, return-rate breaches) pushed to your service desk, and a console for the review queue, cases and SAR recording.

## Run it

With Docker:

```bash
cp .env.example .env
docker compose up --build
```

- Console: http://localhost:8686 (sign in with `sk_ops_demo`)
- API docs: http://localhost:8600/docs

If a port is already taken on your machine, change `ACH_WEB_HOST_PORT`, `ACH_API_HOST_PORT` or `ACH_DB_HOST_PORT` in `.env`. Defaults are deliberately uncommon (console 8686, API 8600, database 5433) so they don't clash with Jenkins on 8080, another dev server on 8000, or a local PostgreSQL on 5432.

Without Docker (Python 3.12+, Node 22, PostgreSQL 16):

```bash
cd backend && pip install -r requirements-dev.txt
ACH_DATABASE_URL=postgresql+psycopg://ach:ach@localhost:5432/ach uvicorn app.main:app --reload --port 8600
cd frontend && npm install && npm run dev        # http://localhost:5173
```

The sandbox seeds three originators (a payroll company, a utility, a supplier), receivers, customer accounts and a day of payments, including one held on a sanctions match.

### Sandbox API keys

| Key | Role |
| --- | --- |
| `sk_ops_demo` | Operator (console, review, sandbox simulator) |
| `sk_admin_demo` | Admin |
| `sk_acme_demo` | Originator: Acme Payroll Services (PPD, CCD credits) |
| `sk_bright_demo` | Originator: Brightway Utilities (PPD, WEB, TEL, CCD debits) |
| `sk_north_demo` | Originator: Northwind Supply (CCD, CTX, IAT) |

### Try it

```bash
# When would a payroll file dated Monday July 5, 2027 settle? (Fed closed: Independence Day observed)
curl -s localhost:8600/v1/reference/settlement-preview -H 'Authorization: Bearer sk_acme_demo' \
  -H 'Content-Type: application/json' -d '{"effective_date":"2027-07-05","sec_code":"PPD"}'

# Release everything queued and settle it now (sandbox only)
curl -s -X POST localhost:8600/v1/ops/run -H 'Authorization: Bearer sk_ops_demo' \
  -H 'Content-Type: application/json' -d '{"force":true}'
```

In the console: **Today → Release and settle all**, then open a settled payment to simulate a return or NOC, or go to **Inbound entries → Simulate an inbound file**.

## Tests

```bash
cd backend && python -m pytest -q                                   # SQLite
ACH_TEST_DATABASE_URL=postgresql+psycopg://ach:ach@localhost:5432/ach_test python -m pytest -q
```

The suite covers the Fed calendar (including Saturday- and Sunday-holiday edge cases), window planning, Nacha file round-trips (PPD, CTX, IAT, returns, NOCs, tamper detection) and end-to-end flows through the API.

## Layout

```
backend/
  app/domain/      fed_calendar.py, windows.py, rules.py   (pure rules; no I/O)
  app/nacha/       record layouts, file builder/parser/validator, IAT/return/NOC builders
  app/services/    originations, screening, release+settlement, returns, rdfi, ledger,
                   monitoring (ITSM), webhooks, scheduler, sandbox simulator, seed
  app/api/         REST routers
  tests/
frontend/          React + TypeScript operations console (Vite)
infra/terraform/   AWS deployment (VPC, ECS Fargate, RDS PostgreSQL, ALB+WAF, KMS, S3 Object Lock, alarms)
docs/              openapi.json, strategy traceability
```

## Before production

Replace the simulated FedACH transmission and core-banking adapters, load the real OFAC lists, plug in an account-validation provider, add Alembic migrations, set a unique `ACH_VAULT_KEY`, and have the Rules Compliance Officer confirm the rule values. The full list is in [docs/strategy-traceability.md](docs/strategy-traceability.md#simplifications-to-replace-before-production).
