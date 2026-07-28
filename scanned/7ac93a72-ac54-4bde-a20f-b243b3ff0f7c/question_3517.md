# Q3517: Pending migration cleanup gap strands old-key funds via Two Logically Distinct Migrations / Pending-State Cleanup Is Necessary in Keeper.completePreviousActiveFinalizedEvent

## Question
Can an unprivileged attacker enter through a user-created outbound or migration-dependent flow that later touches TSS state with two logically distinct migrations or events that differ only in formatting-sensitive fields when pending-state cleanup is necessary to keep funds recoverable, and cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so that it leave a migration pending or half-finalized so value never reaches the new key path, breaking the invariant that migration state must progress atomically enough that user funds cannot become unrecoverable, and resulting in Permanent freezing of funds?

## Target
- File/function: x/utss/keeper/tss_events.go::Keeper.completePreviousActiveFinalizedEvent
- Entrypoint: a user-created outbound or migration-dependent flow that later touches TSS state
- Attacker controls: two logically distinct migrations or events that differ only in formatting-sensitive fields
- Exploit idea: Cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so it can leave a migration pending or half-finalized so value never reaches the new key path.
- Invariant to test: migration state must progress atomically enough that user funds cannot become unrecoverable
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: write a keeper integration test that drives the crafted process/migration flow to finality and inspect whether the active key and pending indexes stay consistent
