# Q2467: Registry params update changes who controls the module via Params Updates Would Change / Malicious Config Would Redirect in msgServer.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when a malicious config would redirect value or strand it, and cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it rotate authority or params from an unprivileged path, breaking the invariant that control of registry authority must be governance-bound only, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/msg_server.go::msgServer.UpdateParams
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can rotate authority or params from an unprivileged path.
- Invariant to test: control of registry authority must be governance-bound only
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
