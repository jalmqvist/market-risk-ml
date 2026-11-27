# MRML B2-S4C1 IG demo execution evidence discovery

The existing broker-state probe now has an `execution-evidence` command.
It collects one sanitized report for one explicitly configured demo account
and EUR/USD EPIC. The new command was verified with deterministic HTTP
fixtures. **A bounded credentialed C1 capture succeeded on 2026-11-27.**
The results below distinguish actual broker observations from offline fixture tests
and from execution semantics that remain unverified.


## Credentialed verification — 2026-11-27

Source: operator-supplied sanitized `ig-demo-execution-evidence-v1` report,
captured at **2026-11-27 18:15:54–18:15:55 UTC** using the read-only
`execution-evidence` command. The source report remains outside the
repository; no raw payload, account ID, balance or session token is recorded
here. Its `result=observed` means a bounded capture within the specified
timing window, **not operational dispatch authorization**.

| Dimension | Credentialed observation | Interpretation and limitation |
| --- | --- | --- |
| IG session and account | Demo; exact active account matched before and after; `dealing_enabled=enabled`; account `ENABLED`, type `CFD`, currency `SEK` | Confirmed for the short capture; broker expiry unknown, local session generation not a durable proof of authenticity |
| Balance fields | Balance, available, deposit, profit/loss fields all present | Values intentionally redacted; presence alone does not verify usable initial margin |
| Exposure | 0 positions; 0 working orders; bounded account-wide collections | Empty at capture time only, not a future flat-account guarantee |
| Timing | Exposure span 0.127700 s; exposure age 0.185058 s; session age 0.532563 s | Within the 10-second collection span and 30-second freshness windows |
| Product | `CS.D.EURUSD.CEE.IP`; `CURRENCIES`; expiry `-`; status `TRADEABLE` | One account/product/time observation |
| Size | `unit=CONTRACTS`; contract size `100000`; lot size `10.0`; `minDealSize.value=0.1`, rule unit `POINTS` | Neither minimum deal-size rule unit nor contract/lot semantics proves an executable increment or exact quantity mapping |
| Order currency | One listed currency, `USD`, `isDefault=false` | Account currency is SEK; an explicit order-currency selection rule remains to be verified |
| Order options | `marketOrderPreference=AVAILABLE_DEFAULT_OFF`; force-open, ordinary stops and controlled risk allowed | Availability is not permission to submit; order validation still required |
| Stop rules | Normal minimum `2.0 POINTS`; controlled-risk minimum `5.0 POINTS`; maximum `75.0 PERCENTAGE`; `minStepDistance=5.0 POINTS` | Point/percentage basis and quote-to-pip mapping still unknown; minStepDistance is not a size increment |
| Risk premium/slippage | Limited-risk premium missing; slippage factor value `50.0`, unit malformed | Preserve absence/malformed status; do not guess units |
| Margin | Factor reported as `3.3300000000000000710542735760100185871124267578125 PERCENTAGE`; USD deposit bands: 0–57.8 at 3.33%; 57.8–289 at 3.33%; 289–462.4 at 3.33%; 462.4+ at 15% | Exact tier basis, boundaries, margin arithmetic and USD→SEK conversion remain unverified; the long factor representation should not be silently rounded |
| Pip/quote | `valueOfOnePip=10.00`; `decimalPlacesFactor=1`; `scalingFactor=1`; `onePipMeans` reported malformed | The parser currently expects a numeric `onePipMeans`, but IG documents this field as **String**; preserve the bounded native text before evaluating its meaning |
| Precision | Size increment and size precision both unsupported | Explicit blockers for order construction |

**Important distinctions:** The report verifies successful authenticated *read
operations* during this capture, but it does not persist independently
authenticated, session-bound provenance to the journal. It does not prove a
broker-server atomic snapshot or establish risk/margin and operator approval
for an actual order.

### Follow-up for B2-S4C2

1. Correct the read-only `onePipMeans` parser to preserve bounded native
   string content rather than marking every nonnumeric value malformed.
   Do not assume the string's units or expose raw private response data.
2. Establish documented versus observed semantics for this exact IG EUR/USD
   product: `CONTRACTS`, `contractSize`, `lotSize`, minimum deal-size
   `POINTS`, quantity increment/precision, executable price/stop units,
   `valueOfOnePip`, and order currency. Unsupported details must remain
   explicit and block normalization.
3. Establish an independently reviewed, exact-product margin calculation,
   including band basis, USD-denominated bands and SEK account-currency
   conversion. Do not normalize the reported 3.330000... factor by rounding.
4. Address optional/malformed slippage factor unit and the reported
   `isDefault=false` order-currency flag without inventing defaults.
5. Keep operational admission and all broker mutation paths disabled.
   A subsequent credentialed read-only capture is useful only after
   the mapping/parser has been reviewed.

## Evidence matrix

| Required evidence | IG source | MRML support | Verification and open questions |
| --- | --- | --- | --- |
| Active account identity and currency | `POST /session` v2, `GET /session` v1, `GET /accounts` v1 | Previous login parsed the active ID and the account call parsed currency. C1 compares all three sources and rechecks session identity and currency after the capture. | Demo login and account discovery passed in B1. The before/after verification is fixture-tested. Session checks bracket separate reads and cannot establish a server-side snapshot. |
| Account status and dealing permission | `GET /accounts` v1 status; v2 login `dealingEnabled` | An exact enabled account is required. C1 preserves the optional login flag as enabled, disabled, missing or malformed. | B1 observed enabled account status. The login flag is documented and fixture-tested. Account- and instrument-specific dealing permission remains to be verified. |
| Account type and balances | `GET /accounts` v1 `accountType`, `balance` | Existing Decimal parser for balance, available funds, deposit and profit/loss. The report shows field presence and a recognized account type. | B1 observed a balance-bearing account. Values stay private. Definitions and applicability to initial margin require review. |
| Session generation, clock and invalidation | `POST /session` v2; `GET /session` v1 with `fetchSessionTokens=false` | Process-local random generation and UTC login receipt time. Reauthentication, client exit, detected account/currency conflict and broker session rejection invalidate the old local state. | Fixture-tested. Actual broker expiry is unknown. The local generation identifies this capture and provides no durable provenance or broker validity guarantee. |
| Full positions and working orders | `GET /positions` v1; `GET /workingorders` v2 | Bounded account-wide arrays with validated rows, unique deal IDs and no recognized pagination markers. Optional account echoes must agree. Only counts and collection windows are included. | B1 credentialed collections were empty. Nonempty, partial, duplicate and conflicting fixtures exercise the new path. Response shape and session bracketing provide coverage evidence, and the two reads remain separate. |
| Exposure timing | Positions and working orders request windows, local UTC and elapsed monotonic time | C1 measures both collection windows and the age at report completion. The earlier collection start to later receipt must be at most 10 seconds. Authentication and exposure must each be at most 30 seconds old at completion. | Fixture-tested, including stale and wide-span cases. Credentialed latency and timing in the review window remain unmeasured. |
| EPIC, product type, expiry and market status | `GET /markets/{epic}` v2 `instrument`, `snapshot` | Existing probe confirmed explicit EUR/USD identity and snapshot status. C1 preserves expiry, checks exact EPIC and rejects conflicting market or snapshot echoes. | A3-S0 observed `CS.D.EURUSD.CEE.IP`, `CURRENCIES`, expiry `-` and `TRADEABLE`. Those are observations for the earlier run. Exact account product and expiry semantics remain open. |
| Order currency and market-order preference | Market v2 `instrument.currencies[].{code,isDefault}`, `dealingRules.marketOrderPreference` | C1 preserves documented code/default rows and preference enum. Conflicting currency defaults are ambiguous. | Documented and fixture-tested. Market-order preference and market status have distinct meanings. Accepted currency and order terms for the configured product remain to be verified. |
| Native trade-size unit and size bounds | Market v2 `instrument.{unit,contractSize,lotSize}` and `dealingRules.minDealSize` | C1 preserves native enum and Decimal fields, plus the raw minimum value and rule unit. Size increment and precision report unsupported. | A3-S0 observed contract size `100000` and lot size `10.0` on the CEE EPIC. Economics, executable size step and quantity precision remain unknown. `minStepDistance` is a distance rule. Snapshot `decimalPlacesFactor` describes quotes. |
| Ordinary and guaranteed-stop constraints | Market v2 `instrument.{stopsLimitsAllowed,controlledRiskAllowed,limitedRiskPremium,slippageFactor,forceOpenAllowed}`; `dealingRules.{minNormalStopOrLimitDistance,minControlledRiskStopDistance,maxStopOrLimitDistance,minStepDistance}` | C1 preserves flags and native rule values separately from their units, including `POINTS` and `PERCENTAGE`. | Documented and fixture-tested. Percentage basis, rounding and applicable guaranteed-stop conditions need exact-product review. |
| Margin factors, deposit bands and currencies | Market v2 `instrument.{marginFactor,marginFactorUnit,marginDepositBands}` | C1 preserves exact factors, native factor unit and band currency, percent, lower and upper bounds. Missing bands and malformed fields remain explicit. | Documented and fixture-tested. Band selection, boundary interpretation and calculation of required margin remain unverified. |
| Quote and pip conversion evidence | Market v2 `instrument.{onePipMeans,valueOfOnePip}`, `snapshot.{scalingFactor,decimalPlacesFactor}` | C1 preserves numeric representations and flags missing or unrecognized forms. | A3-S0 observed source quoted levels near `10990`. No reviewed conversion links them to native quantity, pip value, stop risk or account-currency exposure. |

Sources: [IG session REST reference](https://labs.ig.com/reference/session.html) · [IG accounts](https://labs.ig.com/reference/accounts.html) · [IG positions](https://labs.ig.com/reference/positions.html) · [IG working orders](https://labs.ig.com/reference/working-orders.html) · [IG market details v2](https://labs.ig.com/reference/markets-epic.html) · [Earlier B1/B2 broker-state verification](IG_B1_S1_READ_ONLY_BROKER_STATE.md) · [A3-S0 EUR/USD observations](IG_H1_TIMESTAMP_SEMANTICS.md)

## Operator verification

In the repository root, use the existing private, untracked `.env` with
`MRML_IG_ENVIRONMENT=demo`, the existing `MRML_IG_API_KEY`,
`MRML_IG_USERNAME` and `MRML_IG_PASSWORD`, plus the exact account in
`MRML_IG_EXECUTION_ACCOUNT_ID` and the exact supported EUR/USD product in
`MRML_IG_EPIC`. The operator previously used `CS.D.EURUSD.CEE.IP`.
Exported values take precedence over `.env`. Review the configured
account and product locally before running.

```sh
python -m mrml.data.ig_broker_probe execution-evidence > ig-demo-execution-evidence.json
```

The command uses one bounded authentication POST followed by at most six
bounded GETs, with redirects and retries disabled for the capture. It uses
no history or price endpoint. The existing 262,144-byte response cap and
100-row account/exposure cap apply. A session or identity conflict stops
the capture. The exit codes are 0 for an observed capture within the
timing window, 2 for timing-inconclusive evidence and 1 for a failed capture.
An observed capture can include missing or unsupported fields.

Share only the sanitized JSON. Its version is
`ig-demo-execution-evidence-v1`. It contains a local capture generation,
UTC timestamps, freshness, account and exposure field availability,
market metadata and native rules. Field statuses are `available`,
`missing`, `malformed`, `ambiguous` and `unsupported`. Decimals are
serialized as strings without binary float conversion. Accounts, credentials,
tokens, balances, position and order identifiers, and raw response bodies
are excluded. A failed capture emits a short diagnostic error.

## Mapping to pre-execution evaluation

`OfflineDealingRules` expects one order currency, a contract quantity
increment and precision, and stop bounds in absolute quote units. Market
v2 can report several currencies and mixed rule units, and provides no
documented size increment or quantity precision. Keep native observations
and their units as separate evidence. Require a reviewed, exact-product
mapping for selected currency, size and quote units, applicable ordinary
and guaranteed-stop rules, and validated executable quote semantics before
constructing the normalized inputs.

`OfflineRisk` expects independently established account-currency conversion,
available funds and an exact-intent margin estimate. Account balances and
native margin factors/bands alone do not establish that estimate. A future
contract should bind the selected band and basis, FX conversion, exact
quantity, quote and session to the reviewed calculation. C1 leaves those
inputs unresolved and operational admission disabled.
