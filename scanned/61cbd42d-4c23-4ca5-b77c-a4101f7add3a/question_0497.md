# Q0497: Unprivileged token-config creation bypasses authority via Direct Submission Of Admin- / Attacker Does Not Already in msgServer.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a direct submission of an admin- or gov-gated `uregistry` message when the attacker does not already control admin or governance keys, and cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it whitelist an attacker-chosen token mapping without authorized registry control, breaking the invariant that token whitelist mutations must remain strictly authority-gated, and resulting in Direct theft/loss of funds by wrong-asset mapping?

## Target
- File/function: x/uregistry/keeper/msg_server.go::msgServer.UpdateParams
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a direct submission of an admin- or gov-gated `uregistry` message
- Exploit idea: Cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can whitelist an attacker-chosen token mapping without authorized registry control.
- Invariant to test: token whitelist mutations must remain strictly authority-gated
- Expected Immunefi impact: Direct theft/loss of funds by wrong-asset mapping
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
