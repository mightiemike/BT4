# Q1785: Duplicate vote handling counts one actor twice effectively via Observation Variants Differ Only / Variant Handling Is Only in StakingHooks.BeforeDelegationCreated

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with observation variants that differ only in formatting, canonicalization, or status fields when variant handling is the only guard against semantic collisions, and cause `StakingHooks.BeforeDelegationCreated` to bind a new record or derived action to the wrong live context, so that it use replay, recompute, or variant handling so one logical voter influences the tally more than once, breaking the invariant that each eligible voter should count at most once per ballot outcome, and resulting in Wrong finalization leading to fund loss or freezes?

## Target
- File/function: x/uvalidator/keeper/staking_hooks.go::StakingHooks.BeforeDelegationCreated
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: observation variants that differ only in formatting, canonicalization, or status fields
- Exploit idea: Cause `StakingHooks.BeforeDelegationCreated` to bind a new record or derived action to the wrong live context, so it can use replay, recompute, or variant handling so one logical voter influences the tally more than once.
- Invariant to test: each eligible voter should count at most once per ballot outcome
- Expected Immunefi impact: Wrong finalization leading to fund loss or freezes
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
