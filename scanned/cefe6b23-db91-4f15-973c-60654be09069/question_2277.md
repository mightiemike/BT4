# Q2277: Unprivileged token-config update or removal bypasses authority via Signer Authz Wrapper Crafted / Config Change Would Immediately in MsgAddTokenConfig.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a signer or authz wrapper crafted to confuse authority checks on registry mutations when the config change would immediately affect live user flows if accepted, and cause `MsgAddTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so that it change or remove a live token config from an attacker account, breaking the invariant that live token mappings must not be mutable by unprivileged users, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_add_token_config.go::MsgAddTokenConfig.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a signer or authz wrapper crafted to confuse authority checks on registry mutations
- Exploit idea: Cause `MsgAddTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so it can change or remove a live token config from an attacker account.
- Invariant to test: live token mappings must not be mutable by unprivileged users
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
