# IG demo H1 operational verification (A3-S3)

Operational acceptance is pending successful credentialed verification. Two earlier attempts were rejected by strict UTC-label validation. Use a dedicated PostgreSQL 16 database provisioned for this exercise and the MRML checkout that contains this runbook. Keep captures outside the repository. Use a shell with tracing disabled, a synchronized clock and a Python environment with `requirements-dev.txt` installed.

## Offline preparation

In Bash, load the isolated SQLAlchemy URL (`postgresql+psycopg://USER:PASSWORD@localhost:5432/ISOLATED_DATABASE`) through a hidden prompt. Use a database account scoped to the dedicated database. Verify its identity and PostgreSQL major version before applying migrations.

```bash
set +x
umask 077
read -rsp 'Dedicated MRML database URL: ' MRML_DATABASE_URL; echo
export MRML_DATABASE_URL
python - <<'PY'
import os
from sqlalchemy import create_engine, text

with create_engine(os.environ["MRML_DATABASE_URL"], hide_parameters=True).connect() as db:
    name, version, schema = db.execute(text(
        "SELECT current_database(), current_setting('server_version_num'), current_schema()"
    )).one()
    assert int(version) // 10000 == 16
    print(name, version, schema)
PY
# Run only after confirming the displayed database and schema.
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

Expect head `0004_ig_gap_revisit` at A3-S3. Keep the regular MRML database outside this exercise. To compare journal contents in the dedicated database, run the following before and after ingestion with other writers stopped. Retain the counts and hashes locally. The isolated PostgreSQL recovery test also compares a populated journal row across migration and recovery.

```bash
python - <<'PY'
import hashlib
import os
from sqlalchemy import create_engine, text

tables = ("runs", "signals", "risk_decisions", "orders", "fills", "config_snapshots")
with create_engine(os.environ["MRML_DATABASE_URL"], hide_parameters=True).connect() as db:
    with db.begin():
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        for table in tables:
            rows = db.execute(text(f'SELECT row_to_json(t)::text FROM "{table}" t ORDER BY id'))
            digest = hashlib.sha256()
            count = 0
            for (row,) in rows:
                digest.update((row + "\n").encode())
                count += 1
            print(table, count, digest.hexdigest())
PY
```

Review the exact demo EPIC's product variant against retained market metadata or an independently reviewed IG product specification. Record the reference, date, instrument type, expiry and account product type without identifying the account. The EPIC, currency category and expiry marker alone are insufficient. Stop before the credentialed price request if you cannot establish the variant. If necessary, review the demo platform’s market information for the exact EPIC alongside its matching product specification. The existing client’s `find_market(epic)` can confirm the configured EUR/USD EPIC and current status. Retain only selected instrument fields. Avoid running either of the historical sample probes for this preflight.

Start in source-only mode. Use a new product history in the dedicated database. Pick an explicit UTC hour during a reviewed liquid weekday session that lies at least four hours in the past and inside the default 48-hour catch-up horizon. Choose a window for which the first and terminal hours both have elapsed. Replace the example below with the chosen date and time. With three intervals and an unoccupied terminal label, expect `request_end` exactly three hours after `start` and `point_limit` equal to 4.

```bash
export MRML_IG_EPIC='CS.D.EURUSD.CEE.IP'
export IG_VARIANT='REPLACE_WITH_REVIEWED_PRODUCT_VARIANT'
export IG_START='2026-11-23T09:00:00+00:00'
identity=(--epic "$MRML_IG_EPIC" --variant "$IG_VARIANT")
bounds=(--start "$IG_START" --max-intervals 3 --max-points 4)
evidence=$(mktemp -d)
python -m mrml.data.ig_ingest status "${identity[@]}" > "$evidence/before.json"
python -m mrml.data.ig_ingest plan "${identity[@]}" "${bounds[@]}" > "$evidence/plan.json"
cat "$evidence/plan.json"
```

Review the status and plan before proceeding. Require a non-refused incremental request for the selected range and the expected point budget. The planner computes the end and may shorten it around an existing label or the current time. An exact on-the-hour wire end is necessary for a boundary test. If the stored allowance is unknown in the isolated database, record it as unknown. Keep the default 20-point reserve. The planner does not reserve quota at review time, so compare the ingest plan to the recorded offline plan and flag discrepancies.

## One operator-initiated demo request

Use demo-only credentials and the CLI's fixed demo gateway (`https://demo-api.ig.com/gateway/deal`). Load credentials only when ready. If an approved mapping and policy already exist, keep this initial exercise source-only and use the optional canonical procedure below.

```bash
export MRML_IG_ENVIRONMENT=demo
read -rsp 'IG demo API key: ' MRML_IG_API_KEY; echo
read -rsp 'IG demo username: ' MRML_IG_USERNAME; echo
read -rsp 'IG demo password: ' MRML_IG_PASSWORD; echo
export MRML_IG_API_KEY MRML_IG_USERNAME MRML_IG_PASSWORD
python -m mrml.data.ig_ingest ingest "${identity[@]}" "${bounds[@]}" > "$evidence/ingest.json" 2> "$evidence/ingest-error.txt"
ingest_rc=$?
unset MRML_IG_API_KEY MRML_IG_USERNAME MRML_IG_PASSWORD
printf 'Ingest exit code %s\n' "$ingest_rc"
python -m mrml.data.ig_ingest status "${identity[@]}" > "$evidence/after.json"
python -m mrml.data.ig_ingest plan "${identity[@]}" "${bounds[@]}" > "$evidence/next-plan.json"
```

Inspect the exit code and attempt. One successful first-page response should produce one committed attempt and a retrieval ID equal to the attempt ID. Record the attempt start (before authentication), response receipt and commit. The attempt timestamp is the available local request-start marker. The wire parameters can be reconstructed from the ingest plan and verified against the offline wire-contract test. REST v3 uses `GET /prices/CS.D.EURUSD.CEE.IP` with `resolution=HOUR`, `from=start` and `to=request_end` formatted as UTC wall-clock strings without offsets or fractional seconds, `pageSize=point_limit`, and `pageNumber=1`. With the example bounds and full plan, `from=2026-11-23T09:00:00` and `to=2026-11-23T12:00:00`. The call has no automatic retries or subsequent page requests. The response's validated pagination and exact `snapshotTimeUTC` labels are in the committed outcome for new attempts recorded with A3-S3.

Compare returned labels to the first, interior and terminal hours. Record missing labels and whether the start and terminal labels appear. Terminal presence supports inclusion for that request. Terminal absence can be ambiguous without independent evidence that the terminal candle existed. A sequence consistent with the UTC range supports the client's interpretation for this case. Broader timezone and boundary claims require stronger evidence. Bounded ingestion rejects duplicates, disordered or out-of-window labels, invalid paging and malformed data before they can become committed coverage. For a range-label refusal, retain the sanitized stderr in `ingest-error.txt`. It includes the first offending parsed source label, all parsed UTC labels on the bounded page, UTC bounds, count, validated pagination, validated allowance and the numeric login timezone offset when available. Allowance reported in this diagnostic is inspection evidence only. The failed attempt keeps its existing classification and no retrieval is created. A malformed timestamp or other generic rejection may supply less detail. Record the refusal and stop for review rather than widening the request or shifting timestamps. [IG prices REST reference](https://labs.ig.com/reference/prices-epic.html)

Compare `historical_allowance` before and after, recording the last reported total, remaining points, reset time in seconds, reporting time, local available points, requested point limit and actual count. If comparable reports exist, subtract remaining balances and note elapsed time, possible reset and other clients sharing the quota. A fresh database supplies no baseline. Avoid treating the cap, count and debit as interchangeable.

## Optional experiment after the second rejected response

Preserve the two failed attempts. Use `standard` and the same demo account. Inspect read-only status for the failed 10:00–13:00 UTC job under this variant. If possible, independently verify the account's named timezone and confirm a nonzero offset for the chosen date. A current numeric login offset can corroborate the observation but does not establish the historical zone. The diagnostic uses the offset reported by the existing login on a range-label rejection.

For the same window, use the standard variant's failed job ID while the window remains inside the catch-up horizon and the single replacement remains eligible. If that window has aged out, use a recent, elapsed active-session window away from a DST transition with `--start UTC_HOUR` in place of `--retry-job "$IG_JOB_ID"`. Keep the three-interval, four-point cap. Run the offline plan first and stop if the plan is refused or would shorten the intended window. Do not override a quota refusal.

```bash
identity=(--epic CS.D.EURUSD.CEE.IP --variant standard)
python -m mrml.data.ig_ingest status "${identity[@]}"
# Fill in the job ID from the reviewed failed standard-variant attempt.
read -rp 'Standard-variant failed job ID: ' IG_JOB_ID
bounds=(--retry-job "$IG_JOB_ID" --max-intervals 3 --max-points 4)
umask 077
evidence=$(mktemp -d)
python -m mrml.data.ig_ingest plan "${identity[@]}" "${bounds[@]}" > "$evidence/plan.json"
cat "$evidence/plan.json"
```

After review and loading demo credentials, run once. Record the exit code and retain stdout and stderr locally. Then use read-only status. Do not send a second request to resolve ambiguity.

```bash
MRML_IG_ENVIRONMENT=demo MRML_IG_EPIC=CS.D.EURUSD.CEE.IP   python -m mrml.data.ig_ingest ingest "${identity[@]}" "${bounds[@]}"   > "$evidence/ingest.json" 2> "$evidence/ingest-error.txt"
```

Compare the full UTC label list against the two predictions. For the original wire bounds, UTC parsing predicts 10:00 through 13:00 if inclusive, or 10:00 through 12:00 if terminal-exclusive. Account-local parsing at a verified UTC+1 offset predicts 09:00 through 12:00 if inclusive, or 09:00 through 11:00 if terminal-exclusive. Adjust those predictions to the independently verified offset and chosen window. Compare the terminal label only when both endpoint hours are expected to trade. Record mismatches or inconclusive results explicitly. A repeat rejection with the full shifted sequence would support account-local interpretation for the tested window. It would still leave historical DST handling and general endpoint semantics open.

## Persistence and incremental checks

Use each command in a new process. The post-ingestion `status` invocation reopens PostgreSQL. Inspect the exact stored bid/ask OHLC tuples as decimals through the repository API. Set `IG_END` to the next UTC hour after the plan's `request_end` so the half-open read includes a possible terminal observation. Use the committed retrieval ID from the status when checking the initial response. The read also shows both revisions if a later recheck occurred.

```bash
export IG_END='2026-11-23T13:00:00+00:00'
python - <<'PY'
import os
from datetime import datetime
from sqlalchemy import create_engine, text
from mrml.data.canonical import SourceProduct
from mrml.data.storage import MarketDataRepository

engine = create_engine(os.environ["MRML_DATABASE_URL"], hide_parameters=True)
product = SourceProduct("IG", os.environ["MRML_IG_EPIC"], os.environ["IG_VARIANT"])
start, end = (datetime.fromisoformat(os.environ[key]) for key in ("IG_START", "IG_END"))
repo = MarketDataRepository(engine)
for row in repo.source_revisions(product, "HOUR", start, end):
    print(row.retrieval_id, row.timestamp_field, row.raw_timestamp, row.retrieved_at,
          row.bid, row.ask, row.response_metadata)
with engine.connect() as db:
    for table in ("market_data_ig_ingestion_attempts", "market_data_retrievals",
                  "market_data_observations", "market_data_h1_interpretations"):
        print(table, db.scalar(text(f'SELECT count(*) FROM "{table}"')))
engine.dispose()
PY
```

In a fresh isolated database, expect counts of one attempt, one retrieval, the outcome's observation count, and zero interpretations. A successful empty response can persist a retrieval with no source revisions. Compare source labels and retrieval time to the outcome. Keep bid/ask values local. The bounded client decodes JSON numbers using Python's numeric parser before storage converts the parsed values to exact database decimals. This check establishes persistence of the parsed source values in source units. Broker wire precision beyond the parsed values remains unverified.

Compare the second plan to the first and check that its window avoids stored observations, including a returned terminal label. A newer bounded segment can be eligible if the initial segment left more recent hours. Missing historical labels remain `UNKNOWN` without exact session evidence, and an already attempted missing label is held for explicit review. Check that the verified contiguous checkpoint is unset and that broker candle finality reads `unknown`. Rerun the persistence read and journal comparison to check for changes. Planning and status require no IG access.

## Approved canonical mode and optional recheck

Run the registration example only after explicit review of the exact mapping, timestamp evidence and approval record. A3-S0 supports an empirical start label for this EPIC. Use reviewed, immutable versions and evidence references. Leave acceptance pending if the required approval is unavailable.

```bash
export IG_MAPPING='REPLACE_WITH_APPROVED_MAPPING_VERSION'
export IG_POLICY='REPLACE_WITH_APPROVED_TIMESTAMP_POLICY_NAME'
export IG_MAPPING_EVIDENCE='REPLACE_WITH_REVIEWED_PRODUCT_SPEC_REFERENCE'
export IG_TIMESTAMP_EVIDENCE='docs/audit/IG_H1_TIMESTAMP_SEMANTICS.md'
export IG_APPROVAL='REPLACE_WITH_EXPLICIT_APPROVAL_RECORD_REFERENCE'
python - <<'PY'
import os
from sqlalchemy import create_engine
from mrml.data.canonical import (
    FXInstrument, InstrumentMapping, SourceProduct, TimestampMeaning, TimestampPolicy,
)
from mrml.data.storage import ApprovedTimestampPolicy, MarketDataRepository

fields = ("IG_VARIANT", "IG_MAPPING", "IG_POLICY", "IG_MAPPING_EVIDENCE",
          "IG_TIMESTAMP_EVIDENCE", "IG_APPROVAL")
assert all(os.environ[key] and not os.environ[key].startswith("REPLACE_") for key in fields)
engine = create_engine(os.environ["MRML_DATABASE_URL"], hide_parameters=True)
repo = MarketDataRepository(engine)
product = SourceProduct("IG", os.environ["MRML_IG_EPIC"], os.environ["IG_VARIANT"])
mapping = InstrumentMapping(FXInstrument("EUR_USD"), product, os.environ["IG_MAPPING"])
policy = TimestampPolicy(os.environ["IG_POLICY"], TimestampMeaning.INTERVAL_START,
                         os.environ["IG_TIMESTAMP_EVIDENCE"])
approval = ApprovedTimestampPolicy(mapping, policy, os.environ["IG_APPROVAL"])
repo.register_mapping(mapping, provenance=os.environ["IG_MAPPING_EVIDENCE"])
repo.register_timestamp_policy(approval)
assert repo.resolve_timestamp_policy(product, mapping.version, policy.name) == approval
engine.dispose()
PY
canonical=(--mapping-version "$IG_MAPPING" --timestamp-policy "$IG_POLICY")
python -m mrml.data.ig_ingest status "${identity[@]}" "${canonical[@]}"
python -m mrml.data.ig_ingest plan "${identity[@]}" "${bounds[@]}" "${canonical[@]}"
```

Registration does not retroactively interpret a source-only retrieval. The isolated recovery tests exercise approved canonical persistence with synthetic input. If real canonical verification is still needed, review a distinct bounded window and plan before explicitly invoking the same demo ingest command with `"${canonical[@]}"` appended. Reacquire credentials through the hidden prompts. Use the matching start and end for this window. Verify the canonical result through the repository API.

```bash
python - <<'PY'
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from mrml.data.canonical import CompletionState, SourceProduct
from mrml.data.ig_adapter import TRANSFORMATION_VERSION
from mrml.data.storage import MarketDataRepository

engine = create_engine(os.environ["MRML_DATABASE_URL"], hide_parameters=True)
repo = MarketDataRepository(engine)
product = SourceProduct("IG", os.environ["MRML_IG_EPIC"], os.environ["IG_VARIANT"])
approval = repo.resolve_timestamp_policy(product, os.environ["IG_MAPPING"], os.environ["IG_POLICY"])
start, end = (datetime.fromisoformat(os.environ[key]) for key in ("IG_START", "IG_END"))
result = repo.canonical_history(approval.mapping, policy_name=approval.policy.name,
                                transformation_version=TRANSFORMATION_VERSION,
                                start=start, end=end)
for revision in result.require_observations():
    bar, source = revision.bar, revision.source
    assert bar.start == source.source_timestamp and bar.end == bar.start + timedelta(hours=1)
    assert revision.mapping_provenance == os.environ["IG_MAPPING_EVIDENCE"]
    assert revision.policy_approval == os.environ["IG_APPROVAL"]
    assert approval.policy.evidence == os.environ["IG_TIMESTAMP_EVIDENCE"]
    assert bar.completion.state is CompletionState.UNKNOWN
    assert bar.availability.first_available_at is None
    assert bar.bid.components == tuple(map(float, source.bid))
    assert bar.ask.components == tuple(map(float, source.ask))
    print(source.retrieval_id, approval.mapping.version, approval.policy.name,
          bar.start, bar.end, bar.completion.state, bar.availability.first_available_at)
engine.dispose()
PY
```

A recheck requires an approved-policy observation retrieved during its inferred interval and due after its end. If status lists one and reviewing revisions would add distinct evidence, run the plan below. Confirm it names the desired hour, requests one point and respects quota. Optionally execute one explicit demo ingest after loading credentials again. Compare both retrieval IDs and OHLC tuples through the source reader and record `changed`, `unchanged` or `observation_missing` from the outcome. Candle finality remains unknown in all three cases.

```bash
python -m mrml.data.ig_ingest plan "${identity[@]}" "${canonical[@]}" --recheck --max-points 1
python -m mrml.data.ig_ingest ingest "${identity[@]}" "${canonical[@]}" --recheck --max-points 1
```

## Recovery and refusals

Read status before taking any recovery action. For a staged response, run `python -m mrml.data.ig_ingest recover "${identity[@]}"`. This uses the stored response and policy association without contacting IG. Check that recovery commits under the original attempt/retrieval ID and that invoking recover again does nothing. An uncertain or failed pre-staging attempt may have spent quota. Record its job ID and examine quota holds and current coverage. Only if a replacement is independently authorized, inspect `plan "${identity[@]}" --retry-job JOB_ID --max-intervals 3 --max-points 4` with the original policy options, then invoke `ingest` with the same options if the plan permits it. Use `python -m mrml.data.ig_ingest` for both commands. A replacement is a new attempt with a new retrieval identity. This procedure allows uncertain broker outcomes across process failures.

Stop on demo configuration mismatch, missing approval for canonical options, malformed response, reserve breach, historical exhaustion, throttle hold or a refused window. Inspect status and the retry time. Do not override allowance or provoke a live interruption. Verify rollback, staging, restart, replacement and historical as-of behavior offline using the existing fixtures.

```bash
python -m pytest -q
# Set MRML_TEST_POSTGRES_URL through a hidden prompt to an isolated PostgreSQL 16 URL.
read -rsp 'Isolated test PostgreSQL URL: ' MRML_TEST_POSTGRES_URL; echo
export MRML_TEST_POSTGRES_URL
python -m pytest -q tests/test_ig_ingestion.py tests/test_market_data_storage.py -k postgres
unset MRML_TEST_POSTGRES_URL
```

## Product and session limitations

IG documents `contractSize` and `lotSize` as distinct sizing concepts. Its glossary relates lot size to the minimum deal unit and the amount per point. Market details also expose `onePipMeans`, currency-specific `valueOfOnePip`, and `scalingFactor`, which IG describes in terms of pip value for the instrument's quoted levels. These fields require exact-product and account-product context. A3-S0 observed `100000` for contract size, `10.0` for lot size and quoted levels near `10990`. The similarity to a conventional EUR/USD quote shifted by four decimal places is an empirical plausibility check. No independent exact-product field values or specification establish that conversion here. Preserve source units and exclude this feed from downstream consumers that assume conventional decimal EUR/USD. [IG market details REST reference](https://labs.ig.com/reference/markets-epic.html)

A3-S0 observed an empty `openingHours` list and snapshot status `TRADEABLE`. The market endpoint describes open/close ranges. Evidence for a historical calendar would also need the exact product, effective dates, account time zone, weekdays, DST, holidays and exceptions. Current evidence leaves historical closures unknown. [IG market details REST reference](https://labs.ig.com/reference/markets-epic.html)

Report the sanitized plan and outcome, UTC bounds and wire parameters, raw source labels, paging, counts, timestamps, allowance readings, retrieval identity, policy references, journal comparison and limitations. Omit credentials, auth headers, account identifiers, URLs with credentials, raw responses and local price captures. Keep an absent or ambiguous result explicit.
