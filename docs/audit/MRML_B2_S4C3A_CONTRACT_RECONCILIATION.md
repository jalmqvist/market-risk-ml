# MRML B2-S4C3A EUR/USD contract reconciliation

Evidence matrix version `eurusd-demo-cee-v1`, recorded 2026-11-28.
Scope is the IG demo EUR/USD CFD `CS.D.EURUSD.CEE.IP`. The
account-specific web-platform observations and formulas below are
operator-supplied evidence for that demo account. The public specification
is corroborating or comparative evidence. Account details and market status
apply to their observation times.

- **API, 27 November.** The operator's credentialed read-only diagnostic
  ran at 18:15:54–18:15:55 UTC. The recorded account was enabled, CFD,
  and denominated in SEK. Active account matched before and after the
  capture. Both exposure collections returned zero rows. The market was
  `TRADEABLE` at that capture. [C1 capture](MRML_B2_S4C1_IG_EVIDENCE_DISCOVERY.md)
- **API, 28 November.** Operator attachment
  `ig-demo-execution-evidence-c3.json`, sanitized report schema
  `ig-demo-execution-evidence-v1`. Market observation
  `2026-11-28T10:03:33.796837+00:00`. The demo session and exact active
  account matched before and after, dealing flag was `enabled`, account
  status `ENABLED`, type `CFD`, currency `SEK`, broker session expiry
  unknown. Positions and working orders each returned zero in separate
  bounded account-wide reads. Balance fields were available and values
  were redacted. The result was `observed` within the timing window. It
  supplies neither a server-atomic snapshot nor evidence of usable margin.
- **Web ticket.** Operator-supplied account-specific instrument details
  and margin text in the C3A brief. No independent web-ticket timestamp,
  quote pair or actual margin preview was supplied.
- **Public.** IG's Swedish EUR/USD specification and FX CFD terms, its
  FX margin examples, and the REST field definitions. [EUR/USD spec](https://www.ig.com/se/valuta/markets-valuta/spot-fx-eur-usd) · [FX CFD terms](https://www.ig.com/se/hjalp-och-support/articles/840616-valuta-produktinformation) · [FX margin examples](https://www.ig.com/ie/charges-and-fees/margins) · [REST market v2](https://labs.ig.com/reference/markets-epic.html) · [C2 contract review](MRML_B2_S4C2_EXECUTION_UNIT_SEMANTICS.md)

## Cross-surface matrix

| Property | Observed on demo surfaces | Published evidence | Inference or unresolved boundary |
| --- | --- | --- | --- |
| Identity and permissions | API `CS.D.EURUSD.CEE.IP`, `CURRENCIES`, expiry `-`. Friday `TRADEABLE`, Saturday `EDITS_ONLY`. API market-order preference `AVAILABLE_DEFAULT_OFF`, force-open and stops/limits allowed. Web special information says positions may be rolled into weekday contracts after weekend trading. | REST market status and order preference are different fields. | `EDITS_ONLY` is consistent with weekend timing. Weekday dealing permission and exact weekend/weekday contract linkage still require verification. |
| Contract economics | Web contract value **USD 10 per point**, and **one contract USD 10 per point**. API `CONTRACTS`, contract size `100000`, lot size `10.0`, value of one pip `10.00`. | Standard EUR/USD CFD contract is 100,000 EUR. A pip is 0.0001 USD/EUR and worth USD 10 per standard contract. | The arithmetic corroborates USD 10 per standard-contract pip. API points, ticket points and conventional pips still need an executable mapping. The lot-size field supplies no multiplier or quantity step. |
| Minimum and precision | Web **0.10 contracts**. API `0.1 POINTS` for minimum deal size. Size increment and precision unsupported. | Public Swedish specification lists a minimum of **0.04**. | Preserve the demo minimum as observed. The API rule's dimension, ticket increments and executable precision remain unresolved. |
| Quote basis | API decimal-places factor `1`, scaling factor `1`. C1 pip label was malformed in the old parser. C3 capture records `0.0001 USD/EUR`. | Published conventional pip dimension agrees with the C3 label. | A contemporaneous native executable bid/offer paired to a conventional USD/EUR quote is missing. Historical levels near 10990 do not supply this mapping. |
| Ordinary stop | Web minimum ordinary stop distance **1**, with no unit supplied in the operator record. API minimum normal stop or limit **2.0 POINTS**. | Public Swedish EUR/USD page lists ordinary minimum 2. | Unresolved cross-surface difference. Verify ticket unit, reference quote, level/distance mode and rounding. API maximum `75.0 PERCENTAGE` and minimum step distance `5.0 POINTS` remain native distance fields. |
| Guaranteed stop | Web minimum **10.0%** and special information **“No Controlled Risk.”** API controlled-risk allowed `true`, minimum controlled-risk distance **5.0 POINTS**, and limited-risk premium missing. | Public Swedish EUR/USD page lists guaranteed minimum 5. | Permission and distance disagree across surfaces. No controlled-risk execution mapping or premium is established. |
| Slippage | Web **50%**. API factor value `50.0`, unit malformed. | Web ordinary-stop formula uses a slippage multiplier. | Use an explicitly supplied fraction for offline algebra. The API value does not independently establish a normalized fraction. |
| Margin tiers | Web **Contracts — USD**: **0–57.8: 3.33%**, **57.8–289: 3.33%**, **289–462.4: 3.33%**, **above 462.4: 15%**. API USD bands carry the same boundaries and rates, with no maximum on the top band. API factor is exactly `3.3300000000000000710542735760100185871124267578125 PERCENTAGE`. | Public Swedish specification shows the same thresholds and first three rates, with **6%** at the top. IG's general examples describe allocating quantity across tiers. | Preserve the demo schedule and exact factor separately. Account aggregation, eligibility, boundary and rounding rules and whether IG uses that factor or the band rates remain unverified. |
| Currency and funds | Web and API account currency **SEK**. API has one listed order currency, `USD` with `isDefault=false`, and the deposit bands are USD. | Published conversion descriptions concern their stated contexts, including P/L and adjustments. | Initial-margin USD→SEK FX source, rate, side, timestamp, markup and rounding are unverified. Account funds and accepted order currency require independent verification. |

## Operator-supplied margin formulas and offline coverage

Web no-stop formula:

```text
margin = size × contract_size × price × margin_rate
```

For an ordinary non-guaranteed stop the web platform compares that result
with the following and charges the smaller amount:

```text
(size × contract_size × price × slippage_factor × margin_rate)
+ (size × contract_size × stop_distance)
```

The web text limits the non-guaranteed-stop reduction to **Tier 1**.
Its guaranteed-stop formula is preserved as documentary evidence:

```text
margin = size × contract_size × stop_distance + limited_risk_premium
```

`mrml.data.ig_eurusd_arithmetic` is an offline calculation module. It uses
the standard 100,000 EUR contract, caller-supplied Decimal contract count,
explicit USD/EUR quote and distance units, and rate and slippage fractions.
The ordinary-stop helper requires the caller to establish Tier 1. It
returns both USD formula results and their minimum. The helper does not
derive margin inputs from native broker fields, choose tiers, or convert
USD to SEK. There is no guaranteed-stop helper or admission integration.

The illustrative inputs `0.10` contracts, `1.1000 USD/EUR`, and rate
`0.0333` yield USD `366.30` without a stop. With a `0.50` slippage factor
and a supplied `0.0010 USD/EUR` distance, the alternative yields USD
`193.15`. A supplied `0.0200 USD/EUR` distance yields an alternative of
USD `383.15`, leaving USD `366.30` as the smaller amount. These are
synthetic arithmetic checks. Illustrative fractional counts and distances
establish no ticket increment or accepted stop parameter.

## Monday 30 November non-submitting demo ticket check

1. Open the demo ticket for the exact `CS.D.EURUSD.CEE.IP`. Record the
   instrument name, contract/expiry identity, account currency and capture
   timestamp with timezone. Record whether the product explicitly reports
   tradeable. If identity cannot be confirmed, leave the capture unverified.
2. Record the displayed minimum quantity and unit. In the ticket only,
   try the displayed minimum and a small set of candidate increments,
   for example `0.10`, `0.11`, and `0.20` if the minimum is still
   `0.10`. Record accepted, rejected, or rounded input and the displayed
   quantity. These checks cover only the tested values.
3. Record contemporaneous native bid and offer and conventional USD/EUR
   representations if both are available. Record ordinary-stop minimum,
   unit, ticket distance versus absolute level, applicable quote side,
   and any displayed rounding or limits. Mark unavailable fields explicitly.
4. Compare a no-stop and an ordinary-stop preview for the same exact
   quantity and side, if offered. For each, record quote, timestamp,
   entered and displayed quantity, stop distance or level and unit, any
   shown tier/exposure context, preview amount and currency. Record any
   displayed SEK amount, FX rate, or conversion provenance. Record
   quote or margin changes between the previews rather than assuming a
   fixed input. Keep account identifiers and balances private.

Stop at the preview. Do not submit an order or create a claim. C3A made
no broker requests. Operational blockers remain the executable quantity
lattice and precision, native quote and stop mapping, account-specific
dealing permission, tier application and verified SEK initial-margin
conversion. A preview is evidence for review and does not authorize an
action.
