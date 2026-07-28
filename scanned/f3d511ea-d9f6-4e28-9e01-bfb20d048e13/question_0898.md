# Q0898: Registry params update changes who controls the module via Signer Authz Wrapper Crafted / Attacker Does Not Already in MsgAddTokenConfig.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a signer or authz wrapper crafted to confuse authority checks on registry mutations when the attacker does not already control admin or governance keys, and cause `MsgAddTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so that it rotate authority or params from an unprivileged path, breaking the invariant that control of registry authority must be governance-bound only, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_add_token_config.go::MsgAddTokenConfig.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a signer or authz wrapper crafted to confuse authority checks on registry mutations
- Exploit idea: Cause `MsgAddTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so it can rotate authority or params from an unprivileged path.
- Invariant to test: control of registry authority must be governance-bound only
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
