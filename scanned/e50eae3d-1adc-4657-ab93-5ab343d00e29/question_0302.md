# Q0302: Unprivileged chain-config update bypasses authority via Signer Authz Wrapper Crafted / Message Is Directly User-Submittable in Keeper.UpdateChainConfig

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a signer or authz wrapper crafted to confuse authority checks on registry mutations when the message is directly user-submittable over normal transaction channels, and cause `Keeper.UpdateChainConfig` to overwrite a different live record than the caller should be able to affect, so that it modify live chain settings without already controlling the admin or governance path, breaking the invariant that only authorized actors should be able to change chain enablement, gateways, or confirmations, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/msg_update_chain_config.go::Keeper.UpdateChainConfig
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a signer or authz wrapper crafted to confuse authority checks on registry mutations
- Exploit idea: Cause `Keeper.UpdateChainConfig` to overwrite a different live record than the caller should be able to affect, so it can modify live chain settings without already controlling the admin or governance path.
- Invariant to test: only authorized actors should be able to change chain enablement, gateways, or confirmations
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
