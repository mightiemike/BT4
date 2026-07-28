# Q3862: Unprivileged token-config update or removal bypasses authority via Params Updates Would Change / Message Is Directly User-Submittable in MsgUpdateTokenConfig.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the message is directly user-submittable over normal transaction channels, and cause `MsgUpdateTokenConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it change or remove a live token config from an attacker account, breaking the invariant that live token mappings must not be mutable by unprivileged users, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_update_token_config.go::MsgUpdateTokenConfig.ValidateBasic
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `MsgUpdateTokenConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can change or remove a live token config from an attacker account.
- Invariant to test: live token mappings must not be mutable by unprivileged users
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
