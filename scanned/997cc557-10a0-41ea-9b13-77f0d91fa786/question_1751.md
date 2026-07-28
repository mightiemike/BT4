# Q1751: Self-update message can rewrite another validator identity via State Transitions Around Join/Leave/Tombstone / Eligibility Decisions Must Stay in Keeper.UpdateUniversalValidator

## Question
Can an unprivileged attacker enter through a direct `uvalidator` message submission or a user-triggered flow that consumes UV identity with state transitions around join/leave/tombstone boundaries that attacker-triggered flows can observe when eligibility decisions must stay consistent during one observation lifecycle, and cause `Keeper.UpdateUniversalValidator` to overwrite a different live record than the caller should be able to affect, so that it bind signer and target-validator fields loosely enough that one account edits another UV record, breaking the invariant that only the UV itself should be able to mutate its own identity fields, and resulting in Direct theft/loss or permanent freezing of funds through misrouted cross-chain actions?

## Target
- File/function: x/uvalidator/keeper/msg_update_universal_validator.go::Keeper.UpdateUniversalValidator
- Entrypoint: a direct `uvalidator` message submission or a user-triggered flow that consumes UV identity
- Attacker controls: state transitions around join/leave/tombstone boundaries that attacker-triggered flows can observe
- Exploit idea: Cause `Keeper.UpdateUniversalValidator` to overwrite a different live record than the caller should be able to affect, so it can bind signer and target-validator fields loosely enough that one account edits another UV record.
- Invariant to test: only the UV itself should be able to mutate its own identity fields
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds through misrouted cross-chain actions
- Fast validation: write a keeper test that submits the crafted validator-identity mutation from an unprivileged context and inspect who can vote or which identity is stored
