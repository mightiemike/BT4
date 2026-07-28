# Q2482: Registry params update changes who controls the module via Chain Token Config Payloads / Message Is Directly User-Submittable in MsgUpdateTokenConfig.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with chain and token config payloads that would be dangerous if accepted from an unprivileged account when the message is directly user-submittable over normal transaction channels, and cause `MsgUpdateTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so that it rotate authority or params from an unprivileged path, breaking the invariant that control of registry authority must be governance-bound only, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_update_token_config.go::MsgUpdateTokenConfig.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: chain and token config payloads that would be dangerous if accepted from an unprivileged account
- Exploit idea: Cause `MsgUpdateTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so it can rotate authority or params from an unprivileged path.
- Invariant to test: control of registry authority must be governance-bound only
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
