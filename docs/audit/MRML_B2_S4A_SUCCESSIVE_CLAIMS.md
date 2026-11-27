# MRML B2-S4A successive execution claims

B2-S4A adds ordered claim history and offline preparation for one full
close after a matched demo opening. PostgreSQL admits only the first
preparatory opening claim until B2-S4B implements dispatch admission.
The journal has no broker transport.

## Storage and migration

Through revision `0007_account_gate`, `UNIQUE(scope_id)` served as both the
historical storage limit and the admission guard. Revision
`0008_successive_claims` replaces it with
`UNIQUE(scope_id, claim_number)` and a positive claim number. Existing claims
receive number 1. The migration locks the account and claim tables and refuses
accounts with ambiguous preexisting claim history. Claim IDs, timestamps,
scoped intent and evidence links, intent uniqueness, scoped idempotency,
immutability, and gate transitions are preserved.

The existing PostgreSQL claim trigger locks the account row and checks for
an operational restriction. The new admission trigger runs after it and
accepts only number 1 for an opening under read-committed isolation. A
competing insert for number 1 must satisfy the unique account/number key.
An explicit successor number or a closing intent is refused even through
direct SQL. Rejected inserts roll back claim evidence in the same transaction.
A database error prevents dispatch. The schema can store more than one
immutable claim per account when a later migration supplies a verified
successor admission rule.

The online downgrade locks the same tables and refuses any claim number
beyond 1 before restoring the original key. A populated single-claim
journal can downgrade to `0007` with its gate history intact. The existing
`0007` downgrade retains its own gate-history guard.

## Recovery and closing preparation

`claim_history(scope)` returns claims in admission order.
`account_hold(scope)` and `recover_unresolved(scope)` retain the legacy
conservative view of the original hold. Use `recover_gate(scope, at=...)`
for the latest claim, all outstanding claims, transition history, current
observations, attributable and unmatched positions, and account blockers.
Recovery replays each claim against committed evidence under the account
lock. A later conflicting collection, transaction or assessment can
restore uncertainty. A newer complete collection recorded after the
resolution can refresh exposure when it repeats the same facts. A disappearance
or change of quantity restores uncertainty in this version.

Create a separate immutable `CLOSE` intent with its own idempotency key and
authorization reference. `prepare_closing(scope, intent_id, at=...)`
recovers the account under lock and binds the close to the original opening
claim and intent, the confirmed affected-position deal ID, the exact
residual quantity and opposing direction, the fresh complete positions and
working-order collection IDs, and the gate revision. It requires one
attributable position and refuses other exposure, working orders, restrictions
or uncertainty. It checks that the authorization reference differs from the
opening reference. Verification of the operator and their approval belongs
to B2-S4B. A preparation is an offline result tied to its evidence and check
time. `claim_dispatch` has no argument that can use it as approval.

## Remaining dispatch prerequisites

`consider_action` continues to report all missing dispatch prerequisites.
B2-S4B must verify exact broker session and account identity, complete fresh
exposure and applicable restrictions, dealing rules, risk and margin, exact
operator authorization, and the permitted execution configuration. It must
enforce successor admission from current evidence under the account lock
and insert the claim and dispatch evidence atomically. Commit must precede
the single authorized broker request. Subsequent calls and uncertain outcomes
must never reclaim a historical intent. Close reconciliation, partial closes,
multiple positions and general order management remain outside this slice.

PostgreSQL tests cover concurrent claims and gate restrictions, direct
SQL rejection, recovery, downgrade refusal and upgrade of a populated
`0007` journal. Multi-claim recovery and downgrade refusal use explicitly
seeded fixture history because successor admission is disabled.
