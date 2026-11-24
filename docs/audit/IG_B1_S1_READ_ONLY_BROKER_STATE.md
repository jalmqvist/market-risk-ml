# B1-S1 read-only IG demo broker state

B1-S1 adds typed broker-state reads and an offline-tested operator diagnostic.
The scope is IG demo and uses the existing login, transport and session tokens.
Broker-state checks have used synthetic fixtures. Operator verification on the
demo account is pending.

## Wire contracts

| Request | Version | Scope and model |
| --- | --- | --- |
| `GET /accounts` | 1 | Listed accounts and optional `IGAccountBalance`. Match the login account by opaque ID. [IG reference](https://labs.ig.com/reference/accounts.html) |
| `GET /positions` | 1 | Active account's open positions. `IGPosition` takes the EPIC from `market` and the deal fields, including v1 `dealSize`, from `position`. [IG reference](https://labs.ig.com/reference/positions.html) |
| `GET /workingorders` | 2 | Active account's working orders. `IGWorkingOrder` takes the EPIC and order fields from `workingOrderData`, including `orderSize`, order type and time in force. [IG reference](https://labs.ig.com/reference/working-orders.html) |
| `GET /history/activity` | 3 | Active account's bounded activity. `IGActivity` holds the raw date and available identifiers, type, outcome, channel and detailed fields. `IGActivityAction` holds its own affected deal ID and action type. [IG reference](https://labs.ig.com/reference/history-activity.html) |

The working-orders page labels the route `/working-orders`. The adapter pins
the requested gateway route `/workingorders`, which the established Python
client also uses. Route spelling remains a point for demo verification.
[Python client](https://github.com/ig-python/trading-ig/blob/master/trading_ig/rest.py)

Account IDs, EPICs, deal IDs, deal references and activity identifiers retain
the original string and case. Activity details can mention a different
affected deal ID than the top-level activity deal ID. V3 does not document a
unique activity ID. The adapter retains `activityId` or `activityHistoryId`
when supplied and rejects conflicting values. `activityHistoryId` is
documented in v2. Activity status describes an activity outcome. A deal
reference by itself establishes neither a fill nor a current position.
Record statuses remain absent when IG supplies only a market status.

## Validation and bounds

- Login establishes `currentAccountId`. Each account-scoped read first checks
  that exactly one listed account has that ID and that its status is
  `ENABLED`. Account ordering and the `preferred` flag have no authority.
  Missing, duplicate or unusable active-account data blocks the target GET.
- Collection and entry shapes, required position and order identities,
  directions and sizes, and the activity date field are validated. Empty
  position, working-order and terminal activity collections are valid.
  Optional fields stay absent. Known enum tokens keep their exact spelling,
  as do unknown tokens with the same uppercase enum syntax. Unknown
  account status cannot satisfy the active account check.
- Financial fields accept finite JSON numbers and decimal numeric strings.
  JSON decimals are parsed directly from the body into `Decimal`. Missing
  amounts stay absent. An order EPIC that conflicts with its market EPIC
  rejects the response.
- The diagnostic requires both activity bounds in whole-second UTC, at
  most 24 hours apart, and sends explicit ISO 8601 `Z` timestamps. IG's v3
  table calls the query bounds `DateTime` and the returned date a string.
  It leaves offset acceptance, timezone interpretation and boundary
  inclusion unspecified. The login timezone offset is unused here. Date
  strings in activity records are retained verbatim.
- Activity requests use `detailed=true`, `pageSize=50`, and a one-page
  budget. The response must contain `metadata.paging` with an explicit null
  `next` and a size consistent with the row count and limit. A continuation
  causes refusal and the operator can choose a narrower window. The
  continuation URL is never fetched. One terminal page is broker pagination
  evidence only. It does not establish timestamp semantics or broker-side
  coverage.
- Broker-state bodies and the diagnostic login are streamed with a 256 KiB
  decoded-body cap. Accounts, positions and working orders are capped at
  100 entries per response. Each diagnostic uses one login request and at
  most two GETs with no redirects or automatic retries. Existing IG HTTP
  redaction applies. The CLI prints aggregate counts and relationship
  checks and omits identifiers, amounts, credentials, timestamp labels and
  raw payloads.

## Proposed operator verification

After review and separate authorization, use a known IG demo account in the
portal and verify which account is active. Set `MRML_IG_ENVIRONMENT=demo` and
provide the existing IG authentication variables through the environment
or the local ignored `.env`. Review a short past UTC activity window using
existing activity visible in the portal. Run only the diagnostics needed
for the comparison.

```bash
python -m mrml.data.ig_broker_probe accounts
python -m mrml.data.ig_broker_probe positions
python -m mrml.data.ig_broker_probe working-orders
python -m mrml.data.ig_broker_probe activity --from "$REVIEWED_UTC_START" --to "$REVIEWED_UTC_END"
```

Set both activity variables using `YYYY-MM-DDTHH:MM:SSZ`. Compare counts with the
demo portal for the same account. Check whether the v2 working-order route
and v3 explicit UTC bounds are accepted and whether known events near the
activity boundaries are counted as expected. Record only aggregate counts,
redacted outcomes and whether pagination refused the selected window.
If the portal cannot confirm time interpretation from existing activity,
record the uncertainty. The CLI does not switch accounts or create
broker activity to populate a diagnostic.

## Verification evidence

- Default suite passed 436 tests, including 45 broker-state cases. Nine
  PostgreSQL cases and seven external MPML cases skipped in that run.
- All nine isolated PostgreSQL 16.16 cases passed, including journal
  atomicity and market-data migration and ingestion coverage.
- Alembic upgraded a fresh PostgreSQL database to `0004_ig_gap_revisit`
  and reported no pending schema operations. Mypy, Ruff lint and format,
  and `git diff --check` passed.
