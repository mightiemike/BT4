# Q0908: Registry params update changes who controls the module via Params Updates Would Change / Attacker Does Not Already in Params.Validate

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the attacker does not already control admin or governance keys, and cause `Params.Validate` to trigger an unsafe state-transition edge case, so that it rotate authority or params from an unprivileged path, breaking the invariant that control of registry authority must be governance-bound only, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/params.go::Params.Validate
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `Params.Validate` to trigger an unsafe state-transition edge case, so it can rotate authority or params from an unprivileged path.
- Invariant to test: control of registry authority must be governance-bound only
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
