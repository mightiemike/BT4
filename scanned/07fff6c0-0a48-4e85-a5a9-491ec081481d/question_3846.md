# Q3846: Unprivileged token-config update or removal bypasses authority via Params Updates Would Change / Message Is Directly User-Submittable in msgServer.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the message is directly user-submittable over normal transaction channels, and cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it change or remove a live token config from an attacker account, breaking the invariant that live token mappings must not be mutable by unprivileged users, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/msg_server.go::msgServer.UpdateParams
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can change or remove a live token config from an attacker account.
- Invariant to test: live token mappings must not be mutable by unprivileged users
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
