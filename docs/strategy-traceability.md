# Strategy-to-code traceability

How each part of the ACH Program Strategy is implemented. Paths are relative to the repository root.

| Strategy area | What the strategy asks for | Where it lives |
| --- | --- | --- |
| Discovery: participant role | ODFI, RDFI, Third-Party Sender support | `Originator.is_third_party_sender`; ODFI flows in `services/originations.py`, `services/release.py`; RDFI flows in `services/rdfi.py` |
| Solution design: origination lifecycle | Onboard, intake, pre-release controls, warehouse, batch, transmit, settle, post-settlement | `create_originator` / `activate_originator` (KYB + agreement gate), `create_payment`, `release_due`, `settle_due`, `returns.py` |
| Solution design: receipt lifecycle | Ingest, match, post, availability, credit monitoring, exceptions, returns/NOCs | `rdfi.receive_forward_entries`, `_decision`, `decide_inbound`, `claim_unauthorized`, `create_noc`, `pending_return_batches` |
| System design: one engine, screening before transmission | Gateway → orchestration → rules → screening → file service; append-only ledger | `app/main.py`, `api/deps.py` (auth), `services/originations.py` (pipeline), `nacha/` (file service), `services/ledger.py` |
| ISO 20022 mapping layer | pain.001/pain.008 intake into the same controls; pain.002, camt.054, camt.053 out | `app/iso20022/` (`intake.py`, `reports.py`, `codes.py`), `api/iso20022.py`; see `docs/iso20022.md` |
| API design | REST, idempotency keys, signed webhooks, SFTP/file channel | `api/payments.py` (Idempotency-Key, 409 on body mismatch), `services/webhooks.py` (HMAC `ACH-Signature`), `api/files.py` (`/v1/files/originate`) |
| Business logic: entry state machine | received → held/warehoused → transmitted → settled → returned/reversed | `services/common.py` `TRANSITIONS`, `set_status` |
| Business logic: rules catalog with effective dates | Same Day limit $1M → $10M (Sept 17, 2027), funds availability 9:00 a.m. (Sept 18, 2026), PAYROLL/PURCHASE (Mar 20, 2026), return-rate thresholds | `domain/rules.py` (`DatedValue`) |
| Cut-off times | FedACH windows; internal cut-off 45 min before each Fed deadline | `domain/windows.py` (`WINDOWS`, `CUTOFF_BUFFER`, `plan_settlement`) |
| Weekends | Intake 24/7, nothing settles; weekend effective date → next banking day; evening windows resume Sunday | `windows.window_instances`, `plan_settlement`, tests in `tests/test_calendar_windows.py` |
| Bank holidays | Fed K.8 calendar (Saturday holiday: Fed open Friday; Sunday: closed Monday), governed overrides | `domain/fed_calendar.py`; maker-checker overrides in `services/calendar_service.py` |
| Returns | Deadlines by code (banking vs calendar days), timeliness, account blocking, refunds | `domain/rules.RETURN_CODES`, `services/returns.process_return`, `return_deadline` |
| NOCs | Auto-apply C01–C09 corrections, apply-by date | `services/returns.process_noc`, `apply_noc` |
| Reversals | Only duplicate/wrong receiver/amount/date, within 5 banking days | `originations.create_reversal` |
| Return-rate monitoring | Unauthorized 0.5%, administrative 3%, overall 15%, rolling 60 days | `returns.return_rates`, `check_return_rates` |
| Use cases → SEC codes | Payroll, automatic payments, loans, purchases, P2P, vendor, tax, child support, cross-border | `originations.PURPOSES`, `USE_CASES`; `/v1/reference/use-cases` |
| Business ACH | CCD, CTX (up to 9,999 addenda), IAT (7 mandatory addenda) | `nacha/records.py` layouts, `nacha/builders.iat_addenda` |
| Programming practices | Integer cents, idempotency everywhere, tokenized account numbers | `models.py` (BigInteger cents), `ledger.post(ref=…)`, `core_banking.post(ref=…)`, `common.tokenize` (Fernet vault) |
| Testing | Rules regression, Nacha round-trip, ISO 20022, end-to-end flows | `backend/tests/` (60 tests; run on SQLite and PostgreSQL) |
| Data governance | Canonical domains, tokenization, lineage, retention, access control | Models per domain in `models.py`; vault; file SHA-256 + trace lineage from API → `Payment.trace_number` → `AchFile` → ledger; S3 Object Lock retention in Terraform |
| Compliance: fraud monitoring (Nacha 2026) | Risk-based monitoring for false-pretenses fraud | `screening.fraud_score` (new payee, changed account, WEB validation, velocity, duplicates, amount anomaly) |
| Compliance: RDFI credit monitoring | Detect suspicious inbound credits; delay availability | `screening.credit_monitoring`, hold in `rdfi._decision` |
| Compliance: OFAC | Screen parties before release; IAT party data | `screening.sanctions_screen`; "OFAC cleared" rationale required to release a critical match |
| Compliance: BSA/AML | Typologies, alerts, cases, SAR recording | `screening.aml_checks`, `Alert`, `Case`, `/v1/cases` (`file_sar`) |
| Consumer protection (Reg E) | Unauthorized consumer debits need WSUD; 60-day window | `rdfi.claim_unauthorized` |
| ITSM | P1 on window at risk / missed / recon break; service-desk integration | `services/monitoring.py` (`open_incident`, `run_monitors`, `ACH_ITSM_WEBHOOK_URL`) |
| Settlement and reconciliation | Three-way match per window | `release.reconcile` (Fed advice vs ledger vs core) |
| Audit evidence | Who did what, when | `AuditLog`, `/v1/audit`; operator name via `X-Actor` |

## Simplifications to replace before production

- **FedACH connectivity** is simulated: transmission is acknowledged immediately and the sandbox endpoints stand in for files from the Fed. Production needs a FedLine Advantage or sponsor-bank adapter in `release._build_and_transmit` and an inbound file poller calling `returns.ingest_inbound_file`.
- **Core banking** is a simulated adapter (`services/core_banking.py`) with the same contract you would implement against Fiserv, Jack Henry, FIS or Temenos.
- **Sanctions list** is a fictional sample. Load the OFAC SDN and consolidated lists daily.
- **Account validation** "instant" is a stub; plug in a commercial validation service.
- **IAT returns** are flagged for manual preparation rather than auto-built in IAT format.
- **ISO 20022 XSD validation** is not yet applied (structural and business validation only).
- **Schema migrations**: tables are created at start-up. Add Alembic before the first production schema change.
- **Same Day settlement for inbound returns** is simplified to settle on the day the return file is built.
- Rule values reflect the Nacha Rules as understood in October 2026; the Rules Compliance Officer should confirm them against the current Rules book.
