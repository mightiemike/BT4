# Q3066: Validation accepts a dangerous no-op-to-live config transition via Chain Token Config Payloads / Attacker Does Not Already in MsgAddTokenConfig.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with chain and token config payloads that would be dangerous if accepted from an unprivileged account when the attacker does not already control admin or governance keys, and cause `MsgAddTokenConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it supply a config mutation that looks harmless at basic validation but becomes live and dangerous later, breaking the invariant that authority-gated writes must fully validate dangerous config transitions before commit, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_add_token_config.go::MsgAddTokenConfig.ValidateBasic
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: chain and token config payloads that would be dangerous if accepted from an unprivileged account
- Exploit idea: Cause `MsgAddTokenConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can supply a config mutation that looks harmless at basic validation but becomes live and dangerous later.
- Invariant to test: authority-gated writes must fully validate dangerous config transitions before commit
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
