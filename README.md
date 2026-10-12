# MRML

MRML is the execution and risk layer in the BSVE → MSML → MPML → MRML research
pipeline. This PR provides the Python package foundation and a fail-fast loader
for MPML recommendation bundles. It does not implement broker access, trading
logic, risk logic, or database models.

## Setup

Requires Python 3.11 or newer and pip.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Review `.env` and replace development credentials before starting services;
set `MRML_DATABASE_URL` to use the configured Postgres username, password, and
database. The `MRML_DATABASE_URL` setting is required when calling `load_settings()`.
Settings use the `MRML_` prefix; environment defaults to `dev`. `live` is
intentionally unsupported and requires the explicit `MRML_ALLOW_LIVE=true`
override. OANDA configuration accepts only `practice`; this package does not
connect to OANDA.

## Tests and checks

```sh
make test
ruff check .
ruff format --check .
mypy src
```

Synthetic, non-production parquet and JSON fixtures are committed under
`tests/fixtures/`. Regenerate them with `python tests/make_fixtures.py`. Tests
need no network or database access.

## Local Postgres

Postgres is only for local development; no application code connects to it in
this release. Configure `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`
in `.env`, then start and inspect the service:

```sh
make db-up
docker compose ps
docker compose down
```

The service uses Postgres 16, a named persistent volume, and a readiness
healthcheck. `docker compose down -v` also deletes the local database volume.

## Recommendation bundle

`mrml.contracts.load_bundle(directory)` validates
`strategy_evaluations.parquet`, `recommendations.parquet`,
`recommendations_per_pair.parquet`, and `experiment_manifest.json`. It raises
typed contract errors for missing artifacts or columns, invalid dtypes,
duplicate IDs, invalid ranks, broken evaluation references, unsupported
recommendation metadata schema versions, count mismatches, and invalid required
evidence dates.

Recommendation metadata currently supports schema version `1.0`. The loader
exposes the bundle content hash, manifest provenance, per-pair candidates, and
evidence timing. Evidence dates are optional by default; a bundle with no
evidence end date is not trade-eligible. Callers can configure the evidence
field name and maximum evidence age through the loader arguments.

Assumptions for this scaffold: required `MRML_DATABASE_URL` has no default;
bundle schema version `1.0` is the initial supported version; an ambiguous
`evaluation_for(pair, strategy_id)` result raises a validation error instead
of selecting an arbitrary evaluation. Pair and strategy identifier semantics are
documented in [ADR-001](docs/adr/ADRs.md#adr-001-mpml-artifact-semantics)
(resolved 2026-10-08). The amendment's state provenance behavior follows
[ADR-006](docs/adr/ADRs.md#adr-006-state-resolution-and-recommendation-semantics):
state is not a lookup key in v0. No trading rules are inferred or implemented.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [ADRs](docs/adr/ADRs.md)
- [Changelog](docs/CHANGELOG.md)
