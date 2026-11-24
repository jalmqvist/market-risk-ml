# IG B1-S3 broker identity, lifecycle and reconciliation

This design governs a later operator-authorized IG demo lifecycle. MRML owns
authorization, risk and recordkeeping. The authenticated broker supplies
confirmation and current exposure evidence. Execution has no dependency on
MPML, MSML, recommendations or a GUI.

## Existing contracts

The operational journal migration (0001) created run-owned orders and fills.
`Order.client_order_id` is globally unique, while updates require the original
run ID. `Order.status` and `payload` are replaced in place. `Fill` stores
units, price and slippage as floats and carries both order and run references.
`DailyRunner` writes an intent, calls an injected executor, then records its
returned status in a separate transaction. Its file lock and boolean
reconciliation hook do not coordinate across processes or retain broker
evidence. Existing tests demonstrate scaffold behavior with injected executors.
Risk decisions and run/config provenance remain useful as optional links.
Add execution records beside this journal. Preserve existing rows and APIs.
Use exact PostgreSQL `NUMERIC` and Python `Decimal` for new broker amounts;
historical floating-point fills cannot be recovered as exact broker values.
Market-data revisions 0002–0004 remain separate.

The B1 client pins demo transport, checks the enabled login account and
provides scoped, bounded typed reads. B1-S2 verified three accounts and empty
collections. Nonempty position, working-order and activity rows remain
synthetic. [B1 audit](../audit/IG_B1_S1_READ_ONLY_BROKER_STATE.md)

## Identity and wire evidence

| Identity or evidence | Meaning and correlation |
| --- | --- |
| MRML intent ID | Immutable UUID for one authorized action, including each close, cancellation or modification. A child refers to its initiating intent and exact target broker object. |
| MRML idempotency key | Stable caller-supplied action identity scoped by provider, environment and account. Same key and approved fingerprint return the existing intent. A different fingerprint is a conflict. Matching parameters alone do not establish repeated intent. |
| IG request deal reference | User-defined reference supported by open v2, with documented syntax `[A-Za-z0-9_-]{1,30}`. Allocate and persist once before dispatch. Treat it as correlation. IG documents duplicate/conflict reasons but their replay guarantees and lifetime are unverified. |
| IG acknowledgement | Successful mutation response containing a transaction deal reference. Record the returned value separately; a missing or conflicting reference is anomalous and leaves outcome uncertain. |
| IG confirmation | REST lookup keyed by deal reference. `dealStatus` is acceptance or rejection of the action. Top-level deal ID, object `status`, reason and the array of affected deal IDs and statuses are separate evidence. Affected deals may be opened, amended, deleted, partially closed or fully closed. |
| IG deal ID | Opaque broker identifier, scoped by demo account and recorded with its evidenced role. A confirmation's top-level deal, affected position and working order may have different IDs. An exact evidenced link is required. |
| Activity and actions | Historical evidence. An activity's deal ID, optional activity ID, detailed deal reference and each action's affected deal ID have separate roles. Activity may corroborate a link or raise a conflict. Its absence, a matching EPIC or approximate timing cannot establish identity. |
| Fresh broker state | Position and working-order rows describe objects currently reported for the verified account. A working order represents contingent exposure. Historical acceptance and current exposure are independent. |

| IG contract | Design implication |
| --- | --- |
| `POST /positions/otc` v2 | Submits open request and responds with a deal reference. Preserve approved direction, size, instrument, expiry, order type and relevant options including force-open semantics in the immutable request. [IG reference](https://labs.ig.com/reference/positions-otc.html) |
| `GET /confirms/{dealReference}` v1 | Returns confirmation, reason and affected deals. IG describes REST as a fallback when streaming confirmation is unavailable. Treat confirmation-not-found as inconclusive; timing and retention are unverified. [IG reference](https://labs.ig.com/reference/confirms-deal-reference.html) |
| `GET /positions` v1 and `GET /positions/{dealId}` v1 or v2 | List reports current open positions. Individual lookup is by deal ID for the active account; a missing deal cannot settle historical execution. Version the individual adapter separately before use. [List](https://labs.ig.com/reference/positions.html) · [Individual](https://labs.ig.com/reference/positions-deal-id.html) |
| `GET /workingorders` v2 and `GET /history/activity` v3 | The gateway spelling for the list passed B1-S2. Time interpretation and historical completeness remain unverified. Require bounded reads and preserve activity date strings. [Working orders](https://labs.ig.com/reference/working-orders.html) · [Activity](https://labs.ig.com/reference/history-activity.html) |
| `DELETE /positions/otc` v1 | Later close contract returns a transaction reference. MRML should target the exact position deal ID and reconcile the residual size. The close contract does not document a caller-supplied reference. A lost acknowledgement may leave reference lookup unavailable. [IG reference](https://labs.ig.com/reference/positions-otc.html) |
| `DELETE /workingorders/otc/{dealId}` v2 | Later cancellation design uses exact working-order ID and tracks the returned reference. The reference site displays a hyphenated path. Verify the gateway spelling and version before implementation. Cancellation and execution may race. [IG reference](https://labs.ig.com/reference/working-orders-otc-deal-id.html) |

## State and legal transitions

Track three independent dimensions. The paths below apply to both opening
and closing actions. Close and modification intents must identify a fresh
target and have separate authorization. Their parent remains open or
partially closed until readback supports a different exposure state.

| Dimension | Legal transitions |
| --- | --- |
| Local dispatch | `RECORDED → DISPATCHING → ACKNOWLEDGED`. Before dispatch an intent may be administratively canceled. A crash, timeout, missing acknowledgement or unrecordable response after the durable dispatch claim yields `UNCERTAIN`. Recovery may record an acknowledgement later if independently evidenced; it never resets the claim. |
| Confirmation | `PENDING → ACCEPTED / REJECTED` only from an identity-matched, validated confirmation. Missing, unknown or contradictory evidence remains pending or enters review. Conflicting later evidence opens a discrepancy and preserves both events. Acceptance alone does not establish a fill or current position. |
| Reconciliation | `PENDING → MATCHED / MISMATCH / UNRESOLVED` using the rules below. A newer complete read can reopen a match. Track observed state separately as open, working, partially closed, absent or unknown, with evidence timestamp and account. Missing or stale reads leave current exposure unknown. |

Administrative escalation stops dispatch and records an unresolved disposition
with actor and reason. It does not clear the exposure hold. A terminal action
has reconciled evidence of rejection without attributable exposure, verified
closure or cancellation, or a locally canceled intent with no claim. A
matched opening can finish its action while exposure remains open. Keep
administrative findings separate from broker facts.

## PostgreSQL ownership and atomic boundaries

- **Broker account scope.** Row keyed by provider, `demo` environment and
  opaque account identity. Restrict access to that identity. Serialize claim
  eligibility and the account exposure hold with a row lock or equivalent
  database transaction mechanism. Treat external or unmatched exposure as a
  gate input; it cannot be controlled by the local lock.
- **Execution intent.** UUID, scope FK, idempotency key, operation, parent
  and target links if applicable, immutable versioned parameters and
  fingerprint, authorization provenance and optional originating run FK.
  Unique `(scope, idempotency_key)` and unique allocated request reference
  within scope. Canonicalize exact decimals and order semantics before
  fingerprinting. Keep issued and returned references distinct. Use composite
  foreign keys or equivalent constraints to prevent cross-scope joins.
- **Attempt and evidence journal.** One durable dispatch claim per intent
  for the initial design. Append typed events for claim, acknowledgement,
  uncertainty, confirmation, per-affected-deal result, reconciliation and
  operator disposition. Record event identity, scope, observation time,
  recorded time and a safe reason code. Store structured broker IDs with
  roles, exact amounts and raw broker date labels where needed. Deduplicate
  confirmed evidence by a stable source key when available, otherwise use a
  scoped canonical evidence fingerprint and retain observation provenance.
  Project current state from this evidence or update its projection in the
  same transaction.
- **Read snapshots.** Persist scoped position and working-order reads,
  read intervals and completeness per source, collection rows or normalized
  evidence, plus the intent links and comparison result. Record freshness
  and independent collection times; two GETs are not an atomic broker
  snapshot. Activity pages are supplemental evidence. Keep credentials,
  tokens and full payloads out of the journal and redact broker IDs from
  diagnostic output.

Commit immutable intent and request identity before any dispatch. An identical
repeat returns its recorded state across processes; a conflicting repeat
writes a redacted conflict event and fails. Claim and append the dispatch event
while holding the account gate, then commit before the one authorized network
call. Never keep a database transaction open over HTTP. A durable claim with
no recorded outcome is uncertain even if the crash happened before the send.
No worker or lease expiry may reclaim it for dispatch. If the claim transaction
cannot be verified, skip dispatch. If the outcome transaction fails, keep the
hold, recover from the current database state and read the broker. Commit
each evidence batch, state projection and gate change together. Database
retries may replay deterministic evidence writes with the same event identity.
Before each separately authorized action, require fresh account verification,
a complete exposure baseline and an eligible risk decision. Retain the baseline
evidence IDs with the claim. The first implementation should hold the account
while an exposure-changing claim is unresolved.

## Reconciliation decisions

A `fresh read` below means complete, validated and within a configured
freshness limit for the original verified account. Match exact broker IDs
with known roles plus compatible instrument, direction and Decimal units.
Perform confirmation and follow-up reads within explicit time/request budgets.
When reads disagree or the account cannot be verified, hold new exposure in
that account. A hold is a gate, not a broker finding.

| Case | Evidence and allowed conclusion | Unknown | Further reads and retry | Hold and journal |
| --- | --- | --- | --- | --- |
| 1. Accepted opening | Matched acceptance plus matching fresh position. Opening is reconciled, position reported open. | Individual fill breakdown unless evidenced. | Refresh allowed. No write replay. | No uncertainty hold for this action. Record confirmation, exact link and read. |
| 2. Rejected, no new exposure | Matching ordinary rejection plus fresh reads without attributable exposure. Mark rejected and reconciled. | Any unrelated exposure remains separate. Duplicate/conflict reasons require investigation of the earlier action. | Reads allowed. No replay; a new intent needs distinct authorization. | Clear only this hold after checking other blockers. Record reason and read evidence. |
| 3. Acknowledged, pending | Returned reference; confirmation pending or unavailable. | Disposition and resulting exposure. | Bounded confirmation and current-state reads. No write retry. | Hold. Log acknowledgement and each read outcome. |
| 4. Timeout before acknowledgement | Durable claim; missing acknowledgement. An empty snapshot reports present absence only. | Whether the request reached IG or exposure existed between reads. | Look up a known allocated reference and read current state and bounded activity. No write retry. | Hold. Journal transport class, known reference and observations. |
| 5. Acceptance disagrees with read | Matched acceptance plus absent or conflicting fresh state. | Delayed visibility, intervening action, or wrong identity/size mapping. | Repeat bounded reads, including exact deal lookup if appropriate. No write retry. | Hold. Record mismatch with both sources and their times. |
| 6. Unmatched position | Fresh position with no durable exact link. Exposure exists. | Origin and MRML ownership. | Scoped reads and operator investigation. No implicit adopt, close or submit. | Hold. Record external/unmatched exposure. |
| 7. Duplicate caller request | Scoped key and stored fingerprint. Return existing state if equal; reject conflict if different. | Existing uncertainty remains. | Existing action may be read or reconciled. No second dispatch. | Preserve account gate. Record the duplicate or fingerprint conflict. |
| 8. Restart | Durable claim without result is uncertain; acknowledged state still awaits confirmation; committed evidence can be replayed. | Network outcome where evidence is missing. | Recover scoped reads only for claimed attempts; unclaimed plans still require fresh authorization and gate checks. | Preserve holds across restart. Journal recovery decision and evidence deduplication. |
| 9. Working order present | Exact fresh working-order match establishes contingent exposure. | Execution until independently confirmed and linked. | Refresh working orders, positions and confirmation. No write retry. | Reserve contingent risk; hold if unmatched or uncertain. Journal order ID, terms and read. |
| 10. Partial or multi-deal closing | Per-deal statuses and fresh residual sizes. Reconcile each known affected leg. | Unmatched legs or missing quantities; no inference from a single top-level ID. | Fresh reads per leg. No retry of the close; further action needs separate authorization. | Hold while any leg is unresolved. Journal associations, residuals and evidence. |
| 11. Expired session | Auth/session failure interrupts collection. | Current exposure and reconciliation completeness. | Explicit demo reauthentication and account recheck allow fresh reads. No write retry. | Hold. Record sanitized failure and invalidate incomplete batch for matching. |
| 12. Active-account mismatch | Login account disagrees with persisted scope. | Original account exposure. | Refuse target reads under the wrong scope; resume only after authorized login to the original account. No write retry. | Hold original account. Journal scope mismatch without raw IDs in diagnostics. |

## Unresolved semantics and implementation sequence

Verify reference echo and retention, confirmation visibility lag, duplicate
and conflict semantics, action status and affected-deal cardinality, and
netting and partial-close identifier continuity. Verify cancel/fill race
evidence and correlation when a close loses acknowledgement. The B1-S2
empty results leave activity UTC interpretation, boundaries and completeness
open. Establish read freshness and cross-source timing thresholds before
allowing a write gate to clear automatically.

1. **B1-S4:** Add the provider-neutral exact-decimal evidence types, a
   read-only IG confirmation/individual-position adapter, and deterministic
   completeness, correlation and transition rules. Keep all tests offline.
2. **B2 preparation:** Add additive account-scoped storage and database
   tests for concurrent claims, conflicts, failures, holds and restart.
3. **B2 bounded demo lifecycle:** After separate review and authorization,
   add a one-shot operator-controlled opening path, verify confirmation and
   fresh state, and then implement a separately authorized close and readback.
   Defer working-order changes and expanded execution until those paths have
   their own evidence and authorization.
