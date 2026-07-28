# Q3258: Unprivileged chain-config creation bypasses authority via Params Updates Would Change / Config Change Would Immediately in Keeper.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the config change would immediately affect live user flows if accepted, and cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it make an admin-gated registry write succeed from an unprivileged account, breaking the invariant that only the configured registry authority should mutate chain configuration, and resulting in Direct theft/loss or permanent freezing of funds by malicious reconfiguration?

## Target
- File/function: x/uregistry/keeper/msg_update_params.go::Keeper.UpdateParams
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can make an admin-gated registry write succeed from an unprivileged account.
- Invariant to test: only the configured registry authority should mutate chain configuration
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds by malicious reconfiguration
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
