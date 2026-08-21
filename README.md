# Declarative Migrations promotion E2E

This repository is the **production release-promotion verifier** for Declarative Migrations. It never creates, drops, migrates, or connects to a database. Destructive, failure-injection, engine, permission, recovery, and convergence tests belong to `declarative-migrations-test/declmig-e2e` and its focused scenario repositories, where every target must be ephemeral or explicitly disposable.

The trust role is machine-enforced in `config/repository.json`:

- `declarative-migrations-test/declmig-e2e` produces independent candidate evidence against PostgreSQL and CockroachDB.
- `declarative-migrations/declmig-e2e` consumes one exact workflow run, exact job conclusions, an exact DPM source pin, and exact GitHub artifact SHA-256 digests before promotion.

## Required checks

The `Declarative migrations promotion evidence` workflow has two required jobs:

1. `contract` compiles and tests the verifier, rejects secret-like configuration, proves the repository is evidence-only, and checks that the local DPM source pin matches the independent evidence manifest.
2. `test-evidence` reads the exact public test-organization workflow run and fails closed unless the repository, workflow ID/path, run attempt, event, head branch/SHA, four required jobs, source pin, artifact IDs/sizes/digests, and artifact expiry state all match `pins/test-evidence.json`.

Pull requests run in **candidate mode** and may pin a successful test-organization pull-request run so the verifier itself can be reviewed. Pushes to `main`, schedules, and manual release checks run in **release mode**; release mode accepts only a successful `push` run from the test repository’s `main` branch. A production PR therefore cannot be safely merged while its evidence manifest still names a test pull-request run.

## Pin updates

`pins/source.json` and `pins/test-evidence.json` move together in one reviewed pull request.

- Every commit is a full lowercase 40-character SHA; mutable branches, tags, abbreviated SHAs, and `latest` selectors are forbidden.
- The evidence manifest pins the producer repository, workflow, run/attempt, event, head branch/SHA, source commit, required job names, and each artifact’s immutable ID, byte size, and GitHub SHA-256 digest.
- The producer’s `pins/source.json` is fetched at the exact workflow head SHA and must name the same DPM source commit.
- Candidate evidence must be replaced by a successful test-main push run before production release promotion.

## Product data-plane contract

Product conformance uses one `*-lib-core` persistence authority, installed into API and web servers as the same immutable Zed package:

- the API owns request-serving product reads/writes, authorization, invariants, transactions, idempotency, and audit/outbox behavior through `__api_rw`;
- the web server receives bounded generated reads through `__web_ro` and sends product mutations through the generated API client;
- web-owned session/PKCE/CSRF/cache state is isolated behind `__web_state_rw`;
- a serialized one-shot `__migrator` owns declared DDL, backfills, catalog readback, and the migration ledger;
- Shared Auth owns identity and session assurance, not product-domain authorization or database access.

“Both web and API freely write product tables” is not the fleet default because it duplicates policy and invariant enforcement and expands the credential and audit boundary.

See `config/repository.json`, `pins/source.json`, `pins/test-evidence.json`, `scripts/verify_test_evidence.py`, and `.github/workflows/e2e.yml` for the enforced contract.
