# Q0498: Unprivileged token-config creation bypasses authority via Signer Authz Wrapper Crafted / Config Change Would Immediately in msgServer.UpdateTokenConfig

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a signer or authz wrapper crafted to confuse authority checks on registry mutations when the config change would immediately affect live user flows if accepted, and cause `msgServer.UpdateTokenConfig` to overwrite a different live record than the caller should be able to affect, so that it whitelist an attacker-chosen token mapping without authorized registry control, breaking the invariant that token whitelist mutations must remain strictly authority-gated, and resulting in Direct theft/loss of funds by wrong-asset mapping?

## Target
- File/function: x/uregistry/keeper/msg_server.go::msgServer.UpdateTokenConfig
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a signer or authz wrapper crafted to confuse authority checks on registry mutations
- Exploit idea: Cause `msgServer.UpdateTokenConfig` to overwrite a different live record than the caller should be able to affect, so it can whitelist an attacker-chosen token mapping without authorized registry control.
- Invariant to test: token whitelist mutations must remain strictly authority-gated
- Expected Immunefi impact: Direct theft/loss of funds by wrong-asset mapping
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
