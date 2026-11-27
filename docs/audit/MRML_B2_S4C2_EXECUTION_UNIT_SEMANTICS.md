# MRML B2-S4C2 EUR/USD execution unit semantics

Scope is IG demo EUR/USD CFD `CS.D.EURUSD.CEE.IP`. The empirical source
is the operator’s sanitized 27 November 2026 capture. At that time the CFD
account currency was SEK and both exposure collections were empty. Account
state, balances and dealing conditions are time-specific. C2 used that record
and public documentation and made no new authenticated request. [C1 capture](MRML_B2_S4C1_IG_EVIDENCE_DISCOVERY.md)

## Quantity, pip and stop evidence

| Native field and captured value | Published meaning and supported interpretation | Remaining uncertainty and effect on executable requests |
| --- | --- | --- |
| `instrument.unit=CONTRACTS`; `contractSize=100000` | Market v2 defines `unit` as the trade-size denomination. IG Sweden documents a standard EUR/USD CFD contract as 100,000 EUR, consistent with the capture. An already-established standard contract count can be expressed as base-currency exposure for economic analysis. | Exact admissible API quantities and account-specific term selection remain unresolved. |
| `instrument.lotSize=10.0` | The REST glossary relates lot size to the dealing unit and amount per point. This is a separate field from contract size. Preserve the native value. | No established multiplier or executable increment follows from this field. |
| `dealingRules.minDealSize={value: 0.1, unit: POINTS}`; size increment and precision unavailable | The REST rules carry a value and rule dimension. The OTC API accepts numeric `size` and documents a general ceiling of 12 decimal places. The ceiling says how much precision the API can accept generally. The Swedish public EUR/USD page lists a minimum of `0.04`, which differs from the captured demo rule. | The relationship between the demo minimum rule and submitted contract size, the quantity lattice and product-specific accepted precision are unverified. These block executable sizing. `minStepDistance=5.0 POINTS` remains a distance rule. |
| Position quantity, no nonempty row in the capture | Position collection v1 uses `dealSize`. The v2 individual read uses `size`. MRML preserves each as a native Decimal alongside exact position identity. | No observed nonempty C1 row ties a requested amount to a returned deal size. Reconciliation can compare exact already-established native units when such evidence exists. |
| `instrument.onePipMeans` unavailable in C1 | Market v2 declares a string describing one pip. IG Sweden’s public EUR/USD description states `0.0001 USD/EUR`. The diagnostic now preserves a bounded native string, including this documented form. | The original wire text is unavailable. The public description establishes the economics of one standard pip, while mapping raw demo quote levels and API stop distances still requires exact-product evidence. |
| `instrument.valueOfOnePip=10.00` | The REST field represents pip value. IG Sweden states USD 10 per pip for a standard contract, consistent with 100,000 EUR × 0.0001 USD/EUR per pip. | This supports an economic check for an already-established number of contracts and pips. It does not establish point-to-pip conversion for this API or the account-currency risk amount. |
| `snapshot.decimalPlacesFactor=1`; `scalingFactor=1` | Market v2 describes quote decimal places and a scaling factor used to determine pip value for platform levels. These are quote metadata. A3-S0 historical source labels were near 10990. | No reviewed mapping joins those raw historical levels, a contemporaneous executable bid/offer, and conventional USD/EUR. Quote metadata cannot establish size precision or be used as a universal price multiplier. |
| `minNormalStopOrLimitDistance=2.0 POINTS`; `minControlledRiskStopDistance=5.0 POINTS`; `maxStopOrLimitDistance=75.0 PERCENTAGE` | REST distinguishes ordinary and controlled-risk rules and preserves the separate point and percentage dimensions. IG Sweden’s product page also lists ordinary and guaranteed minimum distances of 2 and 5. The OTC API exposes separate numeric `stopDistance` and `stopLevel`. | Point-to-API-distance scale, quote side and reference price, percentage denominator, boundary rounding and guarantee premiums remain unverified. These block protective-stop construction and executable stop-risk normalization. |
| `instrument.currencies=[USD, isDefault=false]`; SEK account | REST defines the listed codes as available order currencies and OTC `currencyCode` as required and restricted to those codes. USD was the only listed order currency in the capture. | There was no default. Fresh account/product evidence must confirm the currency selection. SEK is the account currency. |

Sources: [IG market details REST v2](https://labs.ig.com/reference/markets-epic.html) · [IG OTC position REST contract](https://labs.ig.com/reference/positions-otc.html) · [IG positions REST reference](https://labs.ig.com/reference/positions.html) · [IG individual position REST reference](https://labs.ig.com/reference/positions-deal-id.html) · [IG Sweden EUR/USD product page](https://www.ig.com/se/valuta/markets-valuta/spot-fx-eur-usd) · [IG Sweden forex CFD terms](https://www.ig.com/se/hjalp-och-support/articles/840616-valuta-produktinformation) · [Earlier H1 source observations](IG_H1_TIMESTAMP_SEMANTICS.md)

## Margin and account-currency evidence

| Native field and captured value | Confirmed interpretation | Remaining uncertainty and effect on executable requests |
| --- | --- | --- |
| `marginFactor=3.3300000000000000710542735760100185871124267578125`; `marginFactorUnit=PERCENTAGE` | REST defines a margin requirement factor with a separate unit. The decimal is exactly the decimal expansion of the nearest IEEE-754 binary64 representation of 3.33, reproducible with `Decimal.from_float(3.33)`. This is consistent with upstream float serialization. MRML reads bounded broker-state JSON numeric tokens as Decimal and retains the exact reported value. | The source does not establish how IG generated the value, how its margin engine rounds or whether it substitutes this factor for a tier rate. Diagnostic output and any later evidence must retain the exact token value. |
| `marginDepositBands`: USD 0–57.8, 57.8–289, 289–462.4 at 3.33%, and 462.4+ at 15% | REST documents a calculation currency, margin percentage and lower/upper band bounds. IG Sweden’s EUR/USD page labels the same thresholds as aggregate contract quantities in USD and shows the same first three percentages. The published general tier method applies the higher rate only to the portion in that tier. | The public top tier is 6%, whereas the demo capture shows 15%. Applicable account schedule, exact boundary/rounding rules, aggregation and the treatment of stops/slippage require confirmation for this account and EPIC. |
| SEK account and USD bands | IG Sweden describes converting foreign-currency P/L and adjustments into SEK when booked. The public product page shows a SEK equivalent for the USD pip value. Those are evidence of currency conversion in their respective contexts. | Applicable initial-margin FX source, timestamp, side, policy or markup and rounding remain unknown. Instrument currency rates and historical prices have no established mapping to the initial-margin conversion. |
| `available` balance field present, amount redacted | The account read exposed that field during the capture. | Required margin, the usable funds comparison and fresh exposure must be independently verified at evaluation time. No balance or margin amount is carried forward from C1. |

Sources: [IG market details REST v2](https://labs.ig.com/reference/markets-epic.html) · [IG Sweden EUR/USD product page](https://www.ig.com/se/valuta/markets-valuta/spot-fx-eur-usd) · [IG Sweden forex CFD terms](https://www.ig.com/se/hjalp-och-support/articles/840616-valuta-produktinformation) · [IG Sweden margin overview](https://www.ig.com/se/sakerhetskrav) · [IG published FX no-stop and tier examples](https://www.ig.com/ie/charges-and-fees/margins)

**Illustration only, unverified for this demo account.** In IG’s published
no-stop example, quote-currency notional follows from contract count ×
base-currency contract size × an applicable quote in quote currency per unit
of base currency. A tiered extension would sum
`contracts_in_tier × EUR_per_contract × USD_per_EUR × tier_percent / 100`
for the applicable portions. A hypothetical SEK estimate would multiply
that USD result by the applicable SEK-per-USD rate. For existing exposure,
the incremental requirement would depend on the broker’s aggregation and
margin treatment before and after the proposed action. Exact demo quote
selection, band and stop treatment, conversion, fees and rounding are missing.
This illustration supplies no executable margin estimate or admission input.

## Parser and next evidence

`onePipMeans` now produces a diagnostic string while retaining the
`ig-demo-execution-evidence-v1` field shape. The parser accepts up to
48 ASCII characters in a restricted numeric-label grammar with optional
USD or USD-per-EUR notation. Missing or null values remain missing. Empty,
unexpected or unsafe values become malformed without echoing their content.
No executable conversion helper uses the label or the public examples.

The smallest follow-up is the read-only evidence needed to resolve these
specific differences:

1. Capture the repaired pip label and obtain the exact demo EPIC’s instrument
   specification or written broker confirmation of the contract-size lattice,
   minimum size, accepted precision, and explicit order currency. A ticket
   displaying a minimum alone does not establish the lattice.
2. Obtain an independently reviewed same-session mapping for native
   executable bid/offer, a conventional quoted level, point/stop units, and
   the reference price, rounding and guarantee conditions for the stop rules.
   A non-submitting demo ticket can supply a comparison if those fields are
   shown. Historical prices remain historical observations.
3. Obtain the account-specific band and boundary rules, stop and slippage
   treatment, and USD-to-SEK rate policy. Compare against an exact-size
   margin preview shown on a non-submitting ticket, if available, with
   timestamp, quantity, currency, quote and exposure context. Confirm the
   provenance of any conversion rate or stop premium used.

Until that evidence is reviewed, `OfflineDealingRules` and `OfflineRisk`
remain fixture contracts. Operational admission stays disabled.
