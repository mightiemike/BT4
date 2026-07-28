# Q3461: Unprivileged chain-config update bypasses authority via Chain Token Config Payloads / Message Is Directly User-Submittable in MsgRemoveTokenConfig.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with chain and token config payloads that would be dangerous if accepted from an unprivileged account when the message is directly user-submittable over normal transaction channels, and cause `MsgRemoveTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so that it modify live chain settings without already controlling the admin or governance path, breaking the invariant that only authorized actors should be able to change chain enablement, gateways, or confirmations, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_remove_token_config.go::MsgRemoveTokenConfig.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: chain and token config payloads that would be dangerous if accepted from an unprivileged account
- Exploit idea: Cause `MsgRemoveTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so it can modify live chain settings without already controlling the admin or governance path.
- Invariant to test: only authorized actors should be able to change chain enablement, gateways, or confirmations
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
