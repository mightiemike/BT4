# Q3918: Join/leave transitions leave a ghost voter in critical paths via State Transitions Around Join/Leave/Tombstone / Attacker Does Not Already in Keeper.UpdateUniversalValidator

## Question
Can an unprivileged attacker enter through a direct `uvalidator` message submission or a user-triggered flow that consumes UV identity with state transitions around join/leave/tombstone boundaries that attacker-triggered flows can observe when the attacker does not already control a privileged UV or admin role, and cause `Keeper.UpdateUniversalValidator` to overwrite a different live record than the caller should be able to affect, so that it use state transitions so a removed or not-yet-added UV still counts or still owns external identity, breaking the invariant that UV lifecycle changes must update all voting and identity surfaces atomically, and resulting in Wrong finalization or permanent freezing of funds?

## Target
- File/function: x/uvalidator/keeper/msg_update_universal_validator.go::Keeper.UpdateUniversalValidator
- Entrypoint: a direct `uvalidator` message submission or a user-triggered flow that consumes UV identity
- Attacker controls: state transitions around join/leave/tombstone boundaries that attacker-triggered flows can observe
- Exploit idea: Cause `Keeper.UpdateUniversalValidator` to overwrite a different live record than the caller should be able to affect, so it can use state transitions so a removed or not-yet-added UV still counts or still owns external identity.
- Invariant to test: UV lifecycle changes must update all voting and identity surfaces atomically
- Expected Immunefi impact: Wrong finalization or permanent freezing of funds
- Fast validation: write a keeper test that submits the crafted validator-identity mutation from an unprivileged context and inspect who can vote or which identity is stored
