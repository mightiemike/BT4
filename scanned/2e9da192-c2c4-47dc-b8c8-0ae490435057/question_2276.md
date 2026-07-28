# Q2276: Unprivileged token-config update or removal bypasses authority via Direct Submission Of Admin- / Attacker Does Not Already in MsgAddChainConfig.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a direct submission of an admin- or gov-gated `uregistry` message when the attacker does not already control admin or governance keys, and cause `MsgAddChainConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it change or remove a live token config from an attacker account, breaking the invariant that live token mappings must not be mutable by unprivileged users, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_add_chain_config.go::MsgAddChainConfig.ValidateBasic
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a direct submission of an admin- or gov-gated `uregistry` message
- Exploit idea: Cause `MsgAddChainConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can change or remove a live token config from an attacker account.
- Invariant to test: live token mappings must not be mutable by unprivileged users
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
