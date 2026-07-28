# Q3123: Migration fee or gas assumptions corrupt the migrated amount via Repeated Actions Meant Strand / Live Outbounds Migrations Depend in Keeper.completePreviousActiveFinalizedEvent

## Question
Can an unprivileged attacker enter through a user-created outbound or migration-dependent flow that later touches TSS state with repeated actions meant to strand pending events or migrations when live outbounds or migrations depend on the evolving TSS record, and cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so that it push a migration-related value path through wrong gas or refund semantics, breaking the invariant that migration logic must preserve the exact amount and ownership of moved funds, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/keeper/tss_events.go::Keeper.completePreviousActiveFinalizedEvent
- Entrypoint: a user-created outbound or migration-dependent flow that later touches TSS state
- Attacker controls: repeated actions meant to strand pending events or migrations
- Exploit idea: Cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so it can push a migration-related value path through wrong gas or refund semantics.
- Invariant to test: migration logic must preserve the exact amount and ownership of moved funds
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper integration test that drives the crafted process/migration flow to finality and inspect whether the active key and pending indexes stay consistent
