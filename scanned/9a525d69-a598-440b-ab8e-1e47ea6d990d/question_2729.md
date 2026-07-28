# Q2729: Key-history lookup diverges from current-key use in live flows via Process Ids, Event Ids, / Attacker Can Create More in Keeper.completePreviousActiveFinalizedEvent

## Question
Can an unprivileged attacker enter through a user-created outbound or migration-dependent flow that later touches TSS state with process ids, event ids, key ids, or chain ids consumed when TSS state transitions finalize when the attacker can create more than one related flow over time, and cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so that it cause one path to use a stale key while another believes a newer key is active, breaking the invariant that all live outbound- or migration-relevant paths must agree on the active key context, and resulting in Direct theft/loss or permanent freeze of funds?

## Target
- File/function: x/utss/keeper/tss_events.go::Keeper.completePreviousActiveFinalizedEvent
- Entrypoint: a user-created outbound or migration-dependent flow that later touches TSS state
- Attacker controls: process ids, event ids, key ids, or chain ids consumed when TSS state transitions finalize
- Exploit idea: Cause `Keeper.completePreviousActiveFinalizedEvent` to push the wrong logical object through a vote or terminal state transition, so it can cause one path to use a stale key while another believes a newer key is active.
- Invariant to test: all live outbound- or migration-relevant paths must agree on the active key context
- Expected Immunefi impact: Direct theft/loss or permanent freeze of funds
- Fast validation: write a keeper integration test that drives the crafted process/migration flow to finality and inspect whether the active key and pending indexes stay consistent
