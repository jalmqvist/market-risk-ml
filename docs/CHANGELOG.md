# Changelog

## Unreleased

-   Added an `mpml-runtime` CI job that clones MPML at
    `ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57`, verifies its git HEAD and
    clean tracked tree, and runs the real-MPML integration and binding tests
    (a skip fails the job). The historical replay test keeps its `90d8ea7`
    pin and is not run in this job.
-   Bound the OHLCV-only PhaseAware boundary to MPML's merged causal
    runtime (`ef6b1b84c7393a1bb4dbd6f92a1442115c6cbb57`) with git-based
    revision verification, a `StrategyRunner` strategy seam, and optional
    `SignalIntent`/`StrategyDecision` provenance fields (size multiplier,
    SL/TP fractions, timestamps; not applied to risk). Non-flat decisions
    without a fixed stop are refused. Real-MPML tests skip without
    `MPML_ROOT`; the frozen fixture is unchanged and its 22 of 32 prefix
    mismatches (phase only) are documented as a strict `xfail`.
-   Added a tested MRML PhaseAware decision boundary that validates
    ordered completed OHLCV history (no caller-supplied phase or prepared
    fields), cutoff, and pinned result provenance without implementing MPML strategy logic.
    The pinned MPML checkout was unavailable, so no current runtime is
    bound and current-decision parity remains unverified. Recorded the
    offline scaffold baseline and existing lint/format failures in the
    PR2 roadmap status.
-   Incorporated ADRs.md into the PR2 documentation update. Clarified
    that ADR-006 continues to defer selector/state serving for v0 and
    added proposed ADR-007 for the causal PhaseAware runtime interface,
    with cross-references from the roadmap and architecture.
-   Expanded the Integration PR2 roadmap into a prompt-ready
    implementation brief: pinned MPML runtime boundary, causal prefix
    parity, future-label exclusion, signal/execution timing, preserved
    TF4/MR42 semantics, fail-closed inputs, and explicit non-goals.
    Clarified the same runtime contract in ARCHITECTURE.md and updated
    README status to distinguish historical replay parity from runtime
    parity.
-   Audited the canonical MPML standalone PhaseAware TF4+MR42 path and
    recorded strategy timing, fold-local indicator behavior, and
    trade/accounting semantics. Added the completed EURUSD fold-0
    fixture with hash-pinned signals, execution timeline, trades,
    accounting diagnostics, and metrics. Replaced the synthetic parity
    stub with fixture-integrity/accounting checks and an opt-in exact
    replay check against the pinned MPML checkout.
-   Added a practice-only OANDA H1 client with retries/timeouts, UTC+1
    H1-to-D1 aggregation, deterministic overlap diffs, and
    incomplete-bar rejection.
-   Added fixed-composition PhaseAware strategy adaptation, pure
    flat-sizing risk limits, fail-closed daily orchestration, demo-only
    execution handoff, and SQLAlchemy journal models/Alembic schema.
-   Added explicit frozen-universe, practice account, risk, evidence,
    and bar boundary configuration; live execution remains blocked.
-   Added account-currency-aware risk sizing; missing quote-currency
    conversions reject new entries rather than assuming parity.
-   Added historical D1 warmup validation and in-run reservation of
    position and currency limits across configured pairs.
-   Added offline runtime/risk/journal tests. Historical MPML
    replay-fixture integrity is now covered; direct StrategyRunner
    binding to MPML remains a separate integration step, as do broker
    reconciliation/order adapter work.

## 0.1.0 - 2026-10-12

-   Scaffolded the Python package, typed environment settings, CI, local
    Postgres, and synthetic fixtures.
-   Added fail-fast validation and provenance access for the amended
    MPML recommendation bundle format.
-   Aligned planning documents with ARCHITECTURE v0.3 / ROADMAP
    2026-10-08 direction (PhaseAware-first v0, no live state resolver in
    v0, ADR status sync).
-   Rewrote ROADMAP.md as a single canonical v0.3 execution plan to
    remove superseded phase ambiguity.
-   Rewrote ARCHITECTURE.md as a single canonical v0.3 document and
    removed transitional split-structure sections.
