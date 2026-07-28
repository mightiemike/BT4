# Q2335: Concurrent-process assumptions break under attacker-timed flows via Repeated Actions Meant Strand / Live Outbounds Migrations Depend in Keeper.completePreviousActiveFinalizedEvent

## Question
Can an unprivileged attacker enter through a user-created outbound or migration-dependent flow that later touches TSS state with repeated actions meant to strand pending events or migrations when live outbounds or migrations depend on the evolving TSS record, and cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so that it reach overlapping TSS state transitions that the module assumes cannot coexist, breaking the invariant that TSS lifecycle must serialize mutually exclusive processes safely, and resulting in Wrong signing state or inability to finalize cross-chain funds?

## Target
- File/function: x/utss/keeper/tss_events.go::Keeper.completePreviousActiveFinalizedEvent
- Entrypoint: a user-created outbound or migration-dependent flow that later touches TSS state
- Attacker controls: repeated actions meant to strand pending events or migrations
- Exploit idea: Cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so it can reach overlapping TSS state transitions that the module assumes cannot coexist.
- Invariant to test: TSS lifecycle must serialize mutually exclusive processes safely
- Expected Immunefi impact: Wrong signing state or inability to finalize cross-chain funds
- Fast validation: write a keeper integration test that drives the crafted process/migration flow to finality and inspect whether the active key and pending indexes stay consistent
