# Q2079: Unprivileged token-config creation bypasses authority via Params Updates Would Change / Message Is Directly User-Submittable in MsgAddChainConfig.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the message is directly user-submittable over normal transaction channels, and cause `MsgAddChainConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it whitelist an attacker-chosen token mapping without authorized registry control, breaking the invariant that token whitelist mutations must remain strictly authority-gated, and resulting in Direct theft/loss of funds by wrong-asset mapping?

## Target
- File/function: x/uregistry/types/msg_add_chain_config.go::MsgAddChainConfig.ValidateBasic
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `MsgAddChainConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can whitelist an attacker-chosen token mapping without authorized registry control.
- Invariant to test: token whitelist mutations must remain strictly authority-gated
- Expected Immunefi impact: Direct theft/loss of funds by wrong-asset mapping
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
