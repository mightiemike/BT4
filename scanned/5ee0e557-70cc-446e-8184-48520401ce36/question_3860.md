# Q3860: Unprivileged token-config update or removal bypasses authority via Signer Authz Wrapper Crafted / Message Is Directly User-Submittable in MsgUpdateParams.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a signer or authz wrapper crafted to confuse authority checks on registry mutations when the message is directly user-submittable over normal transaction channels, and cause `MsgUpdateParams.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it change or remove a live token config from an attacker account, breaking the invariant that live token mappings must not be mutable by unprivileged users, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_update_params.go::MsgUpdateParams.ValidateBasic
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a signer or authz wrapper crafted to confuse authority checks on registry mutations
- Exploit idea: Cause `MsgUpdateParams.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can change or remove a live token config from an attacker account.
- Invariant to test: live token mappings must not be mutable by unprivileged users
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
