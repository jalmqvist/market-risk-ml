# MRML B2-S3 account gates and restart recovery

The append-only account gate separates an immutable historical claim, a
claim-specific reconciliation finding, and the account's current blockers.
Broker confirmation and current exposure continue to come from the B1-S4
opening rules and the B2-S2 persisted inputs. All verification in this stage
uses synthetic evidence.

## State and legal transitions

`GateJournal.recover_gate(scope, at=...)` returns the historical claim,
any outstanding claim uncertainty, ordered gate history, current blockers,
latest position and working-order observations, and the latest assessment
when its inputs can be reconstructed. Account identity starts unverified.
Missing or partial collections, stale reads, missing confirmation,
conflicting or unreplayable evidence and unsupported outcomes are distinct
blockers. A known attributable position, unattributed positions and
contingent working orders are tracked independently.

| `account-gate-v1` transition | Evidence and effect |
| --- | --- |
| `OPERATIONAL_RESTRICTION` | Records an actor and a sticky restriction. It neither changes claim uncertainty nor releases other blockers. There is no unrestricted administrative override. |
| `OPENING_RESOLVED` | Requires replay agreement on `MATCHED / OPEN_POSITION_MATCHED` for the scoped claim's intent, using the evidence requirements below. The position remains known exposure. Unattributed positions and contingent orders remain separate blockers. |
| `REJECTION_RESOLVED` | Requires replay agreement on `MATCHED / REJECTION_WITHOUT_ATTRIBUTABLE_EXPOSURE`. The same requirements for scope, coverage, ordering and freshness apply. Independent exposure or restrictions remain blockers. |

`restrict` and `resolve_claim` require the caller's expected revision,
stable transition UUID, opaque actor identity and timezone-aware decision
time. Exact UUID replay returns the existing transition. Changed use of the
same UUID and a stale expected revision fail. No-op restrictions and
redundant resolution of a currently resolved claim are rejected.

## Evidence requirements

The resolution loads the immutable assessment and its exact input
associations under the scoped account lock, then replays the supported
opening algorithm. It checks the actual result and reason and requires the
assessment to have been evaluated after the durable claim. The selected
transaction observations must cover all currently known typed facts for
that intent. At least one confirmation is required. Transaction observation
times must fall between the claim and evaluation. A different simultaneous
or later assessment must replay and agree on the inputs and result.

Both exposure inputs must be complete account-wide collections. A targeted
lookup cannot qualify. Both must follow the latest selected confirmation
observation. When a collection interval is available, its start must follow
that observation. The offline policy allows at most 30 seconds from the
start or, if absent, observation time to decision. Ordering uses comparable
local observation times and makes no assertion about broker execution time.
An unselected collection with a later or equal observation or recording
time blocks reliance on the proposed evidence. Late-arriving contradictory
transaction evidence also prevents reliance on an earlier resolution.

Recovery revalidates recorded resolutions against their original decision
clock and all presently known evidence. Later incompatible or incomplete
observations block current reliance on the resolution and can restore
outstanding uncertainty in the projection. The original transition remains
in history. Mere age adds a stale-exposure blocker without erasing the
historical finding. Reads recovered after a restart have no new freshness
or authentication evidence.

## Database boundaries

Alembic `0007_account_gate` adds `broker_gate_transitions` and an insert
guard on the existing claims table. It changes no historical records. The
transition stores the scope, previous and resulting revision and blocker
snapshots, reason, actor, policy, decision and recording times, and exact
claim, intent, assessment and collection IDs for a resolution. PostgreSQL
locks the account row for gate and claim insertions. A sequence guard and
scope/revision uniqueness reject races. Scoped foreign keys and an insert
guard verify claim and assessment links and the referenced collection IDs.
Update, delete and truncate guards protect the history.

The database checks structural relationships. The repository replays
correlation for semantic verification, and recovery independently verifies
a resolution before relying on it. A structurally valid forged `MATCHED`
row cannot resolve uncertainty if its evidence cannot reproduce the result.
The downgrade requires an online emptiness check and refuses nonempty gate
history. Existing claim immutability and uniqueness per scope and intent
are preserved.

**Multiple historical claims remain unsupported.** B2-S1's existing
`account_hold` and `recover_unresolved` views remain conservative
compatibility APIs over the claim table. For current claim-specific
uncertainty, use `recover_gate`. The claim insert guard refuses a new claim
under a recorded operational restriction, even through the legacy
repository or direct SQL. The historical uniqueness constraint also
continues to refuse a second claim after an offline resolution. Future
multi-claim support needs its own independently tested database gate before
that constraint can change.

## Consideration and future work

`consider_action` validates the proposed request shape and reports gate
blockers and missing prerequisites. This version cannot independently
verify a fresh IG session, current exposure, risk and margin, exact
operator authorization, or an enabled execution configuration. It returns
a restrictive result even after claim-specific uncertainty resolves.
Recovery performs only database reads and a scoped lock for a consistent
view. It cannot submit, retry, poll, or authorize an action.

Before a controlled demo order, MRML still needs verified broker identity
and baseline completeness, independently reviewed action authorization and
risk integration, a validated execution boundary, and a reviewed strategy
for repeat claims and their account-level constraints.

## Verification

PostgreSQL tests cover initial and recovered states, both resolution paths,
external exposure, incomplete, stale and superseding evidence, replay
failures, transaction identity, competing transitions and claims, a
commit-time interruption, database bypass attempts and migration
preservation. No IG requests were made.

- Default suite passed 523 tests. It skipped 82 isolated PostgreSQL
  cases run separately and seven external MPML cases.
- Targeted B1/B2 offline suite passed 132 tests. All 82 isolated cases
  passed on PostgreSQL 16.16, including 23 new gate cases.
- A fresh migration reached `0007_account_gate` with no Alembic schema
  drift. Upgrades preserved populated history and the downgrade guard
  refused to discard gate transitions.
- Mypy, Ruff lint and format, and `git diff --check` passed.
