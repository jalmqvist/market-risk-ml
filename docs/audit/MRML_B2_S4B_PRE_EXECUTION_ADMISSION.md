# MRML B2-S4B pre-execution admission

B2-S4B implements an offline evaluator for a bounded IG demo opening and
a separately proposed full close. Operational admission remains disabled.
There is no broker mutation transport and no route from an evaluation
result to a dispatch claim.

## Evidence and authority

`AdmissionJournal.evaluate_offline` compares an exact proposed request with
a committed intent and its canonical fingerprint. Its versioned result binds
the request, scope, intent, supplied evidence, recovered gate revision and
ordered claim history, evaluation time and evidence expiry. It returns
`REFUSED` for contradictions or a blocked account and `INCOMPLETE` when
verification is missing. `ADMISSIBLE` is reserved for a future trusted
implementation. Even a complete, internally consistent fixture reports
that broker and operator verification are unavailable. The result is
read-only and reproducible from the same fixtures and committed inputs.

The offline session fixture identifies one enabled account, its currency, a
session generation, a verification time and expiry. Exposure, rules and risk
fixtures refer to that generation. Exposure must use the currently selected
complete account-wide positions and working orders. Their collection windows
must start during the fixture session, fit inside the 30-second freshness
window, and span at most 10 seconds from the earliest collection start to the
latest observation. Separate broker reads can give different views within
that interval. A current conflicting observation, an operational restriction
or unresolved claim keeps the action blocked.

B2-S2 accepts supplied broker observations. Their scope, digests and replay
results support consistency checks. Trusted broker provenance requires a
controlled acquisition path that binds authenticated IG demo account reads
and their completeness to the active session, invalidates them on expiry,
account switching or reauthentication, and persists only sanitized evidence.
The existing read-only IG adapter has in-memory credentials and account reads
but does not provide this durable binding.

Offline authorization binds an operator label and unique reference to the
account, intent fingerprint, exact request, issuance and expiry. The evaluator
also detects a scoped authorization reference already associated with a
historical claim. This is an offline check of supplied approval terms.
Authenticated operator interaction and transactional one-time consumption
are still required for dispatch. The evaluator accepts no credential,
session token, or caller-selected admission outcome.

## Dealing and risk evaluation

The narrow fixture contract supports market orders, contract quantities,
explicit instrument currency and contract value per absolute quote-level
movement, known minimum size and increment, and exact Decimal precision.
Opening requires a supplied protective stop within the explicit bounds.
Unsupported guaranteed stops and take-profit options are refused. A trusted
IG adapter must validate the conversion from native instrument units,
order and stop rules, and executable quote semantics. Historical market-data
prices supply no dealing quote.

Opening requires flat account-wide exposure and an empty claim history.
The offline checks enforce the supplied maximum quantity and notional,
position limit, daily-loss and drawdown bounds and account-currency stop risk.
The estimate uses exact quantity, stop distance, contract value and explicit
currency conversion. Margin and available funds are supplied account-currency
evidence tied to the exact intent and session. Their origin and calculation
still need independent verification. MRML's general-purpose risk controller
continues to serve its existing callers.

Closing reuses the B2-S4A full-close checks within the account lock. It
requires one resolved opening, exact affected deal ID and opposite direction,
unchanged residual quantity, complete current exposure and a separate
authorization reference. Position reduction has no opening risk budget.
Partial closes, another opening, and multi-position or live accounts remain
outside the supported lifecycle.

## PostgreSQL and readiness

No migration is needed. Version `0008_successive_claims` remains the schema
head. The evaluator reads an account and replays its committed gate under
the account row lock. It writes no claim, evidence, evaluation or approval
records. The existing legacy first-opening claim remains a preparatory
journal mechanism. Both the legacy repository gate and the PostgreSQL
trigger continue to refuse successors. Historical claims, ordering,
scoped links and evidence remain immutable.

A future operational path requires an authenticated acquisition and
authorization boundary, reviewed IG constraints and risk/margin evidence,
production execution configuration, and serialized revalidation against
concurrent restrictions and conflicting evidence. That transaction must
consume exact operator approval once and commit the claim, dispatch evidence
and verification linkage atomically. Only a separate future broker
integration can make a single mutation after that commit. No automated
retry or claim replacement is permitted. B3 is awaiting those capabilities.
