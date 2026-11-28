# MRML IG demo prototype v0.1 pre-release readiness
Status review: 2026-11-28. Documentation-only assessment of the merged main
branch through PR #24. This is a **manual demo-trade platform** milestone,
not a portfolio risk or MPML strategy release.

## Acceptance boundary

**Working prototype v0.1** means an operator can use a small CLI to select
one exact IG **demo** account and the reviewed EUR/USD EPIC, verify current
dealing and exposure, authenticate and explicitly approve one **OPEN**, commit
its one-time authorization and durable claim **before** an at-most-once broker
mutation, independently confirm/read back the resulting position, then
authenticate and approve a distinct exact **full CLOSE**, independently confirm
no residual position or working order, and reconcile both actions with durable
journal evidence. A simulated test, a successful login, or an opening alone
does **not** meet acceptance. A crash, stale evidence, conflict, failed read,
unknown result or lost commit receipt must stop without an automatic broker
write retry. Live endpoints remain unsupported.

Minimum per-trade guardrails remain mandatory: exact demo scope/session,
complete fresh account exposure, valid EUR/USD order quantity, protective stop
and currency/quote mapping, funds and margin in account currency, limited
exposure and an authenticated one-time approval. Broader portfolio controls,
daily strategy automation, MPML recommendations, MSML/DL integration, GUI,
multiple instruments/accounts and production operations are **not prerequisites**.

## Evidence-backed implementation inventory

| Layer | Current state | Pre-release implication |
| --- | --- | --- |
| IG connectivity and H1 ingestion | Credentialed bounded demo reads and PostgreSQL source-only ingestion/readback verified | Reuse existing code; historical H1 parity/DST work is not a trade-lifecycle gate |
| Account and exposure | Credentialed account/session checks and empty positions/working-orders read; 2026-11-27 TRADEABLE, 2026-11-28 EDITS_ONLY | Need fresh trade-time data and tests against nonempty confirmed exposure |
| EUR/USD economics | C1/C2/C3A: 100,000 EUR contract, 0.0001 USD/EUR pip, USD 10/contract pip, 0.10 displayed minimum, offline Decimal formula tests | Executable size lattice, bid/offer-to-quote convention, native stop-distance units and applicable SEK initial-margin conversion not yet established |
| Account-specific margin | Operator web and API both show USD bands 3.33/3.33/3.33/15%, account currency SEK; web provides no-stop and ordinary-stop margin formulas | No validated operational account-currency margin estimator; public 6% top tier must not replace demo 15% |
| Execution journal | B2-S1/S2 PostgreSQL immutable intent, preparatory claim, observations and replay; schema `0008_successive_claims` | Historical claims are **not** approval or permission to send; current database still rejects CLOSE/successor operational claims |
| Recovery/gate | B2-S3/S4A offline gate and closing preparation; B2-S4B offline evaluator always REFUSED or INCOMPLETE | Need authenticated input provenance and transactionally guarded real admission; no trusted ADMISSIBLE path |
| Operator approval | B2-S4D1 designed protected PAM-backed issuer, exact request, expiry <=5 min, immutable issuance/consumption | No verified workstation PAM isolation, issuance record, operator verification or atomic consumption |
| IG broker mutation | Existing IG adapter read-only; nonempty confirmation and position response shapes unverified against the broker | Both OPEN and CLOSE transport, bounded one-shot send, confirmation/readback and uncertainties are unimplemented |
| End-to-end operator CLI | Existing read-only diagnostics and individual persistence commands | Manual OPEN/CLOSE/status/reconcile CLI with sanitized audit acceptance remains unimplemented |
| MPML and broad risk | Existing independent research adapters and risk scaffold | Reusable later, not blockers except the minimum executable safety checks above |

Evidence: [roadmap](../ROADMAP.md),
[C1](MRML_B2_S4C1_IG_EVIDENCE_DISCOVERY.md),
[C2](MRML_B2_S4C2_EXECUTION_UNIT_SEMANTICS.md),
[C3A](MRML_B2_S4C3A_CONTRACT_RECONCILIATION.md),
[D1](MRML_B2_S4D1_OPERATOR_AUTHORIZATION_DESIGN.md),
[offline admission](MRML_B2_S4B_PRE_EXECUTION_ADMISSION.md).

## Proposed remaining integration checkpoints

These checkpoints are a planning estimate, not evidence that a broker write
has succeeded. C3B and D2A may run in parallel; the remainder is largely
serial because execution and close depend on trusted upstream contracts.

1. **C3B — non-submitting trade-ticket verification.** While EUR/USD is
   tradeable, compare exact EPIC, displayed accepted size candidates, source
   bid/offer and conventional USD/EUR quote, ordinary stop level/distance and
   preview, account-currency margin and conversion. Record source and time.
   No order submission. Unresolved size/stop/margin terms block B3.
2. **D2A — protected operator-authentication feasibility.** Locally verify
   the proposed PAM interaction, terminal handling, issuer/runner separation
   and PostgreSQL roles. No privileged installation by CI or unattended
   fallback. Simplify the design only after a security review.
3. **D2B — independently authenticated approval issuance.** Implement
   exact immutable intent binding, trusted human confirmation and finite
   expiry. Keep consumption and dispatch disabled.
4. **Trusted acquisition and operational admission.** Bind active IG demo
   session, complete fresh account exposure, native rules, reviewed risk and
   margin evidence to the exact approved request; atomically consume approval
   with the operational claim under the account lock. Enforce database-backed
   uniqueness and fail-closed commit recovery. Preserve legacy preparatory
   claim semantics.
5. **B3 — one-shot controlled demo OPEN.** Implement the demo-only mutation
   with fixed endpoint, no automatic retry, stored acknowledgement,
   independent confirmation/readback and reconciliation. Investigate
   missing/nonempty broker schemas offline first. No automatic order.
6. **B4 — independently authorized full CLOSE and restart tests.** Before
   allowing an OPEN in the prototype acceptance environment, verify a
   safely admitted CLOSE can follow a resolved OPEN in the same journal.
   Close only the exact affected deal and full remaining size; independently
   verify final flat state and handle ambiguous responses without replay.
7. **Minimal terminal UX and release gate.** Provide documented
   connect/status, deliberate open, position/reconcile and deliberate close
   actions; sanitized journal report, operator instructions, test evidence,
   and regression/PG/negative-path coverage.

## How to measure distance

At this review **0 of 2 broker mutation types (OPEN, CLOSE) have been
implemented or demonstrated**, and **0 of 1 full broker lifecycle has been
completed**. Most supporting foundation is in place, but the missing pieces
are safety-sensitive integration rather than small cosmetic tasks.

The planning estimate is **roughly 6–8 focused implementation/verification
increments after C3B/D2A**, depending on how much the trusted acquisition,
claim admission, write transport and close lifecycle need to be separated.
This is **not a calendar estimate**. Count successful evidence-based acceptance
gates, not file count, tests alone, or percent complete. Complexity may rise
if the actual workstation cannot enforce the PAM issuer boundary, if quote/
margin semantics conflict, or if IG confirms unexpectedly shaped nonempty
responses. Do not equate feature counts with time remaining.

For a **functional proof of broker connectivity only**, the first successfully
verified OPEN is an intermediate milestone; it does not meet the safe,
recoverable v0.1 platform definition. Optional research and strategy work
can begin after the full manual OPEN/CLOSE path is shown, without requiring
a broad institutional trading platform.
