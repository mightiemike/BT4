# Q2573: Canonicalization collapses safe and unsafe variants into one tally via Observation Variants Differ Only / Variant Handling Is Only in StakingHooks.BeforeDelegationCreated

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with observation variants that differ only in formatting, canonicalization, or status fields when variant handling is the only guard against semantic collisions, and cause `StakingHooks.BeforeDelegationCreated` to bind a new record or derived action to the wrong live context, so that it change formatting-sensitive fields until honest voters appear to agree on different semantics, breaking the invariant that variant handling must preserve every field that changes execution outcome, and resulting in Wrong finalization with direct loss or permanent freezing?

## Target
- File/function: x/uvalidator/keeper/staking_hooks.go::StakingHooks.BeforeDelegationCreated
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: observation variants that differ only in formatting, canonicalization, or status fields
- Exploit idea: Cause `StakingHooks.BeforeDelegationCreated` to bind a new record or derived action to the wrong live context, so it can change formatting-sensitive fields until honest voters appear to agree on different semantics.
- Invariant to test: variant handling must preserve every field that changes execution outcome
- Expected Immunefi impact: Wrong finalization with direct loss or permanent freezing
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
