# Q3714: Stale process finalization overwrites the active key via Repeated Actions Meant Strand / Attacker Can Create More in Keeper.completePreviousActiveFinalizedEvent

## Question
Can an unprivileged attacker enter through a user-created outbound or migration-dependent flow that later touches TSS state with repeated actions meant to strand pending events or migrations when the attacker can create more than one related flow over time, and cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so that it cause an old or parallel process to become authoritative after a newer state already exists, breaking the invariant that only the one correct active TSS process should be able to define the active key, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/keeper/tss_events.go::Keeper.completePreviousActiveFinalizedEvent
- Entrypoint: a user-created outbound or migration-dependent flow that later touches TSS state
- Attacker controls: repeated actions meant to strand pending events or migrations
- Exploit idea: Cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so it can cause an old or parallel process to become authoritative after a newer state already exists.
- Invariant to test: only the one correct active TSS process should be able to define the active key
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper integration test that drives the crafted process/migration flow to finality and inspect whether the active key and pending indexes stay consistent
