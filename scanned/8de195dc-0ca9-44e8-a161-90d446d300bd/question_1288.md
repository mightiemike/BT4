# Q1288: Authz wrapping weakens registry authority checks via Params Updates Would Change / Config Change Would Immediately in Keeper.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the config change would immediately affect live user flows if accepted, and cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it reach a registry mutation through a wrapper path the module does not intend to trust, breaking the invariant that wrappers must not relax the authority boundary for registry writes, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/msg_update_params.go::Keeper.UpdateParams
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can reach a registry mutation through a wrapper path the module does not intend to trust.
- Invariant to test: wrappers must not relax the authority boundary for registry writes
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
