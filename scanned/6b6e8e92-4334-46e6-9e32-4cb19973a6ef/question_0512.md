# Q0512: Unprivileged token-config creation bypasses authority via Params Updates Would Change / Config Change Would Immediately in MsgUpdateTokenConfig.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the config change would immediately affect live user flows if accepted, and cause `MsgUpdateTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so that it whitelist an attacker-chosen token mapping without authorized registry control, breaking the invariant that token whitelist mutations must remain strictly authority-gated, and resulting in Direct theft/loss of funds by wrong-asset mapping?

## Target
- File/function: x/uregistry/types/msg_update_token_config.go::MsgUpdateTokenConfig.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `MsgUpdateTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so it can whitelist an attacker-chosen token mapping without authorized registry control.
- Invariant to test: token whitelist mutations must remain strictly authority-gated
- Expected Immunefi impact: Direct theft/loss of funds by wrong-asset mapping
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
