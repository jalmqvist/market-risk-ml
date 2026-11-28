# MRML B2-S4D1 operator authorization design

Design version `demo-operator-approval-v1`. Scope is one configured Linux
workstation, one configured IG demo CFD account, a local terminal, and
PostgreSQL. The first opening and its full close each require a fresh
operator decision. This document specifies future interfaces and transaction
invariants. D1 makes no runtime or schema changes.

## What the current implementation establishes

| Contract | What the record establishes | Where authority is missing |
| --- | --- | --- |
| `ExecutionIntent.authorization_id` | Caller-supplied, nonempty opaque string stored with the immutable intent. Idempotent replay returns the original intent and authorization reference. | The caller can choose any label. There is no issuer, authentication, expiry, revocation, or consumption record. |
| Request fingerprint | SHA-256 of journal request v1's canonical JSON. The canonical request includes the exact supported operation terms and normalized Decimal values. | A consistency identifier. Anyone with the request can calculate it. Scope and operator identity are separate from the fingerprint. |
| `OfflineAuthorization` | A fixture supplies operator label, reference, scope, intent, request, fingerprint and validity dates. Admission compares these fields and checks references used in historical claims. | A nonempty name and internally consistent fixture only. `evaluate_offline` cannot establish operator authority, fresh broker provenance or operational admission. |
| `ADMIN_REVIEW` evidence | An append-only administrative reason and a required opaque actor ID on scoped intent evidence. Gate restrictions also carry caller-supplied actor provenance. | The actor label is unauthenticated. Review evidence does not consume approval or resolve a broker outcome. |
| Dispatch claim | Immutable scoped record and dispatch evidence written together under the account row lock. Revision 0008 and the repository allow the first preparatory OPEN. The database rejects a successor or CLOSE. | A durable uncertainty hold. Existing claims have no authenticated approval link and confer no permission to send. |
| Gate, observations and close preparation | Replay checks persisted evidence and the gate keeps exposure separate from claim uncertainty. `prepare_closing` binds an exact full-close target and distinct authorization reference to current local evidence. | Stored observations do not by themselves prove authenticated broker provenance. Close preparation does not authorize or create a claim. |

The proposal builds on the existing claim and offline evaluator contracts.
It does not change the meaning of historical preparatory claims.
[Successive claims audit](MRML_B2_S4A_SUCCESSIVE_CLAIMS.md) ·
[Offline admission audit](MRML_B2_S4B_PRE_EXECUTION_ADMISSION.md) ·
[Account gate audit](MRML_B2_S3_ACCOUNT_GATE_RECOVERY.md)

## Trust boundaries and authentication choice

| Threat | Design response and trust assumption |
| --- | --- |
| Operator approves the wrong action | Render the committed request on the trusted terminal, show scope and every execution term, then require an action-specific confirmation. This reduces mistakes. The operator still has to check the display. |
| Local process invents approval or supplies another name | Require fresh OS-backed authentication inside a separately protected issuer. Use the canonical identity returned by the configured authentication path and a deployment allowlist. Caller arguments, inherited environment, OS username strings and terminal access provide no proof. |
| Request or target changes after approval | Compare the committed intent UUID, scope, version, full canonical bytes and fingerprint with the immutable approval. Recompute the fingerprint and check the presentation context. Any changed term needs a new intent and approval. |
| Expiry, parallel use, restart or uncertain broker outcome | Use the database clock, immutable revocation or consumption, account-row serialization and unique claim binding. Treat uncertainty as a reason to stop and recover by reading. An expired or consumed approval cannot be reused. |
| Process modifies the journal | Separate issuer, reader and future guarded admission privileges. The application cannot write issuance or consumption rows directly. PostgreSQL owners, superusers and migration owners remain trusted and can bypass database controls. |
| Credential or token leaks | Read the authentication secret through a protected local terminal conversation with echo disabled. Do not accept secrets in argv, environment, stdin pipelines, config, SQL, records, or diagnostics. Keep passwords, broker credentials and bearer tokens out of normal logs and audit records. |

The OS, reviewed local executable and configuration, PAM stack, system and
database clocks, database owner and operator's terminal are in the trust
boundary. Malware controlling the operator's trusted terminal can spoof the
display or capture credentials. Root can replace the helper or PAM policy,
read secrets and change database state. A database owner can fabricate
history. This design provides no protection from those compromises.

| Option | Fit for this workflow |
| --- | --- |
| Existing OS login plus terminal confirmation | Minimal dependencies and easy to use. A process in that session could impersonate the prompt or approve without a fresh identity check. Insufficient on its own. |
| Independent PAM-backed issuer | Uses the workstation's identity and account policy, with no MRML password store. An isolated local helper requires a protected interactive terminal and fresh authentication on every issuance. CI and headless calls cannot issue. Requires a deployment-specific PAM and OS isolation check. |
| Independently verifiable short-lived credential | A detached, audience-bound proof signed by an independent issuer could support an offline verifier. It still needs trusted issuance, exact request binding, expiry and database consumption. A bearer token adds leakage and replay exposure. A shared key available to the runner supplies no independent identity. |
| Hardware signing or an external identity provider | Stronger separation is possible with a reviewed hardware-backed signing and display path. It adds key or provider lifecycle, dependencies and recovery work beyond one local operator. Revisit if the trusted-workstation assumption changes. |

**Selected design for D2 feasibility testing:** a fixed, administrator-installed
local helper with a dedicated OS identity and isolated issuance credentials.
It opens the controlling terminal and uses a dedicated PAM service for
fresh `pam_authenticate` and account checks. Only the configured canonical
operator identity may issue. There is no cached login, permissive fallback,
caller-selected PAM service or unattended path. The helper reads the
committed intent and writes the immutable approval through an issuer-only
database interface. The runner and ordinary repository roles cannot invoke
that interface as an issuer. A username, the helper's effective UID and a
successful elevation command alone are insufficient.

D2 must verify that the actual PAM stack can perform independent
reauthentication under the chosen deployment identity, enforce the account
allowlist, and fail on missing modules or disabled accounts. It must verify
protected TTY access, trusted executable and configuration ownership, role
separation and absence of inherited secret-bearing environment or file
descriptors. Use a reviewed local launcher with a fixed entry point.
If this setup needs broad privileges or a permissive PAM stack, leave
issuance disabled and review another independently authenticated path.
Tests can substitute a verifier interface for negative paths and contract
checks. Test fixtures and CI cannot mint an operational approval.

## Exact approval and terminal workflow

The future immutable issuance row holds:

- Random unique authorization UUID and authentication-evidence UUID.
  The latter identifies the issuer's embedded receipt with authenticated
  principal, mechanism and policy identifier, authentication time and
  decision time. These identifiers are audit references.
- Provider, **demo** environment, exact account scope, operation and
  immutable intent UUID. Preserve canonical request version and
  fingerprint, and bind all exact canonical operation terms through the
  immutable intent FK. Keep the approved rendering version and verified
  semantic context needed to reproduce what was shown, including native size
  and stop units, order and account currencies and product/expiry identity.
- Issuance and expiry timestamps. Issuance follows reauthentication and
  confirmation. Use a database-issued time and a maximum lifetime of
  **five minutes**, with a half-open validity window `[issued_at, expires_at)`.
  Authentication evidence must be present and fresh for this same
  interaction. Issue and consume windows are independent of the shorter
  broker evidence limits.

Reserve the authorization UUID before creating the current immutable intent,
whose `authorization_id` is required. Reservation is an untrusted reference.
The protected issuer binds it to the exact committed intent after the
interaction. An expired or revoked reservation requires a new authorization
and new immutable intent and idempotency key under the current intent
contract. Where an allocated request reference is required, reserve a new
scoped reference to preserve the original intent's immutable identity. A previously claimed action remains in recovery.

1. Require a trusted interactive terminal and a verified presentation
   context. Load the committed canonical request and render it deterministically.
   Show demo and an unambiguous account alias tied to the exact scope,
   instrument/EPIC and expiry, OPEN or CLOSE, BUY or SELL, exact quantity
   and native unit, order type and time in force, all applicable options
   and protective stop mode, distance or level and its unit. For CLOSE
   show the exact deal ID privately and the full residual quantity.
   Display order and account currencies separately. If the close wire
   request has no currency field, label the verified position currency as
   context. Display risk and margin only from verified evidence, with
   currency and observation time. Missing required fields or unresolved
   units block issuance. Do not invent defaults for disabled options.
2. Explain that approval records consent to the displayed request, with
   a validity of at most five minutes. Show the confirmation deadline and
   the maximum approval expiry in UTC before approval. The recorded expiry
   is the earlier of that displayed cutoff and five minutes from issuance.
   Require fresh
   reauthentication and an explicit action-specific confirmation bound to
   this rendering, scope and request fingerprint. A short displayed
   challenge can help catch wrong-action errors. The challenge is neither
   an authentication factor nor a transferable credential.
3. Recheck the exact request and context before issuing. Reject changed
   content, cancellation, timeout, authentication failure and a
   noninteractive session. Display the persisted expiry on success. There
   is no `--yes` override, CI issuance or implicit dispatch.

Bind stable reviewed product/unit and presentation identifiers at approval.
Record relevant risk evidence references if they supplied displayed
figures. At future admission independently verify the current demo session
and account, exposure and its freshness, dealing rules, margin/risk and
permitted operation. An IG session identifier from approval cannot supply
freshness. A new authenticated IG session may supply replacement evidence
only for the same exact scope and terms. A changed executable interpretation
or breached approved bound invalidates the approval. Any market-order price
or risk tolerance shown as an approval constraint must be explicit and
enforceable at admission.

## Proposed PostgreSQL lifecycle and crash rules

Use immutable issuance, revocation and consumption. Derive `ISSUED`,
`REVOKED`, `EXPIRED` and `CONSUMED` from records and time. No update can
reset expiry or turn a consumed or revoked approval into an unused one.
Expiry needs no writer. A revocation requires the protected issuer or an
independently authenticated administrative authority and an audit reason.
Retain provenance and reasons in append-only
history with scoped references and restricted readers.

A later admission migration must acquire the same account row lock for
revocation and consumption. The guarded transaction loads the issued
approval and exact intent, revalidates identity, request binding, current
evidence, gate, operation policy, and unique authorization/claim state.
Check the database clock after lock acquisition immediately before
consumption. Revocation winning the lock first blocks consumption.
Consumption winning first remains terminal. A subsequent stop or incident
uses the account's restrictive recovery mechanisms.

Insert the claim, dispatch evidence and immutable consumption link in **one
transaction**. The consumption link is unique by approval and by claim.
Composite constraints bind approval, claim, intent and scope. The future
operational claim path must require its matching consumption link at
commit, for example with a deferred constraint check, while the
consumption FK requires the matching claim. Historical preparatory claims
retain their classification and cannot become operational by acquiring a
link. Restrict direct DML and disable the legacy preparatory route for
the operational role. The database must reject either half on its own.
The account lock and claim policy also have to prevent two unresolved
exposure-changing claims. Do not relax revision 0008 as part of D1/D2.

- A known rollback writes neither half. The guarded transaction can be
  reevaluated while the approval is valid, after verifying there is no
  claim and acquiring fresh evidence.
- A timeout or disconnect at commit is indeterminate. The initiating
  process stops. Recover from the authoritative primary by approval and
  intent ID. A committed claim remains consumed and held even with no
  acknowledgement. If state is unavailable or the outcome cannot be
  determined, keep the account blocked for review.
- A committed claim found after process termination or lost commit receipt
  is never a dispatch token. Any later one-shot sender needs a verified
  successful commit in its current invocation and must still respect
  expiry and its pre-send evidence bounds. If approval or evidence expires
  while commit returns, stop and retain the claim. No automatic write
  retry, claim replacement or dispatch after restart is permitted.
- After a future broker timeout or ambiguous response, retain the hold and
  recover by reading and reconciliation. Empty exposure alone cannot
  establish that a request never reached the broker.

## OPEN and CLOSE are separate actions

**OPEN** permits one initial bounded demo opening, with no existing positions,
working orders or unresolved claims. The chosen size, native units,
protective stop and currency need independent verification. Revalidate
authenticated account and current exposure, intent-specific risk and opening
margin at admission. A preliminary or fixture approval does not qualify.

**CLOSE** requires a fresh independent approval, a new intent, exact
confirmed affected-position deal ID, exact residual quantity, opposing
direction and current full exposure and reconciliation evidence. A change
in target or residual quantity invalidates the approved close. The OPEN
approval has no authority over the CLOSE. Specify the risk-reducing close
checks separately from opening margin. Do not enable partial closing,
working-order actions or successor claims through this design.

## D2 proposal and acceptance specifications

D2 should first prove the local issuer can reauthenticate the configured
operator on the actual deployment and that the runner cannot manufacture
issuance. Then implement only trusted request rendering and independently
authenticated issuance, with its narrowly scoped persistence and expiry
contract. Keep the operational admission, consumption, successor claims
and broker send paths disabled. A separate transaction milestone must
implement and verify the paired claim/consumption boundary under PostgreSQL
before B3. Review the full closing path before enabling any opening.

| Verification | Expected outcome | Stage |
| --- | --- | --- |
| Exact request, scope and verified presentation | Same canonical request, version, fingerprint and intent UUID bind to a single authenticated issuer receipt. Changed fingerprint, canonical bytes, account, environment or execution terms fail. | D2 |
| Wrong operator or missing/expired authentication | Caller name, wrong principal, absent/expired authentication and headless/CI initiation fail. No approval row is created. | D2 |
| Duplicate authorization UUID and expiry | Conflicting duplicate fails. A retry may read the original issuance with its original expiry. Exact expiry instant and any longer-than-five-minute validity are refused for use. | D2 issuance, later consumption |
| Concurrent consumption, revoke race and split transaction | One paired claim and consumption at most. Revocation first blocks. Failure between inserts leaves neither persisted. A lone operational claim or lone consumption is rejected at commit. | Later PostgreSQL tests |
| Crash after consumption and uncertain commit | Restart reads immutable history. Known consumed stays consumed. Lost commit receipt cannot trigger a send. Unavailable authoritative state keeps the account blocked. | Later PostgreSQL and transport-boundary tests |
| Separate CLOSE | OPEN approval fails for CLOSE. New exact CLOSE approval requires the verified deal ID and full residual quantity. Missing deal ID, changed quantity or wrong direction fails. | D2 binding, later admission |

These are acceptance specifications. D1 introduces no authentication
implementation, operational approval, admission, consumption, successor
dispatch or authenticated IG request.
