# Q0698: Unprivileged token-config update or removal bypasses authority via Signer Authz Wrapper Crafted / Malicious Config Would Redirect in Keeper.UpdateTokenConfig

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a signer or authz wrapper crafted to confuse authority checks on registry mutations when a malicious config would redirect value or strand it, and cause `Keeper.UpdateTokenConfig` to overwrite a different live record than the caller should be able to affect, so that it change or remove a live token config from an attacker account, breaking the invariant that live token mappings must not be mutable by unprivileged users, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/msg_update_token_config.go::Keeper.UpdateTokenConfig
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a signer or authz wrapper crafted to confuse authority checks on registry mutations
- Exploit idea: Cause `Keeper.UpdateTokenConfig` to overwrite a different live record than the caller should be able to affect, so it can change or remove a live token config from an attacker account.
- Invariant to test: live token mappings must not be mutable by unprivileged users
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
