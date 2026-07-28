# Q1883: Unprivileged chain-config update bypasses authority via Params Updates Would Change / Attacker Does Not Already in MsgAddTokenConfig.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the attacker does not already control admin or governance keys, and cause `MsgAddTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so that it modify live chain settings without already controlling the admin or governance path, breaking the invariant that only authorized actors should be able to change chain enablement, gateways, or confirmations, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_add_token_config.go::MsgAddTokenConfig.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `MsgAddTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so it can modify live chain settings without already controlling the admin or governance path.
- Invariant to test: only authorized actors should be able to change chain enablement, gateways, or confirmations
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
