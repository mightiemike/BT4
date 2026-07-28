# Q0365: Pending migration cleanup gap strands old-key funds via User-Created Outbound Flow Eventually / Honest Validators Later Act in Keeper.completePreviousActiveFinalizedEvent

## Question
Can an unprivileged attacker enter through a user-created outbound or migration-dependent flow that later touches TSS state with a user-created outbound flow that eventually depends on TSS state or fund migration state when honest validators later act on whatever TSS state the chain stores, and cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so that it leave a migration pending or half-finalized so value never reaches the new key path, breaking the invariant that migration state must progress atomically enough that user funds cannot become unrecoverable, and resulting in Permanent freezing of funds?

## Target
- File/function: x/utss/keeper/tss_events.go::Keeper.completePreviousActiveFinalizedEvent
- Entrypoint: a user-created outbound or migration-dependent flow that later touches TSS state
- Attacker controls: a user-created outbound flow that eventually depends on TSS state or fund migration state
- Exploit idea: Cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so it can leave a migration pending or half-finalized so value never reaches the new key path.
- Invariant to test: migration state must progress atomically enough that user funds cannot become unrecoverable
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: write a keeper integration test that drives the crafted process/migration flow to finality and inspect whether the active key and pending indexes stay consistent
