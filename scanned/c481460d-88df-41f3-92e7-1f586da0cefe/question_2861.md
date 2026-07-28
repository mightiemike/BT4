# Q2861: Authz wrapping weakens registry authority checks via Signer Authz Wrapper Crafted / Message Is Directly User-Submittable in msgServer.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a signer or authz wrapper crafted to confuse authority checks on registry mutations when the message is directly user-submittable over normal transaction channels, and cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it reach a registry mutation through a wrapper path the module does not intend to trust, breaking the invariant that wrappers must not relax the authority boundary for registry writes, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/msg_server.go::msgServer.UpdateParams
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a signer or authz wrapper crafted to confuse authority checks on registry mutations
- Exploit idea: Cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can reach a registry mutation through a wrapper path the module does not intend to trust.
- Invariant to test: wrappers must not relax the authority boundary for registry writes
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
