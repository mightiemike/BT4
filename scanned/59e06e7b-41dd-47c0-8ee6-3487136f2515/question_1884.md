# Q1884: Unprivileged chain-config update bypasses authority via Direct Submission Of Admin- / Config Change Would Immediately in MsgAddTokenConfig.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a direct submission of an admin- or gov-gated `uregistry` message when the config change would immediately affect live user flows if accepted, and cause `MsgAddTokenConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it modify live chain settings without already controlling the admin or governance path, breaking the invariant that only authorized actors should be able to change chain enablement, gateways, or confirmations, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_add_token_config.go::MsgAddTokenConfig.ValidateBasic
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a direct submission of an admin- or gov-gated `uregistry` message
- Exploit idea: Cause `MsgAddTokenConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can modify live chain settings without already controlling the admin or governance path.
- Invariant to test: only authorized actors should be able to change chain enablement, gateways, or confirmations
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
