# Q1484: Validation accepts a dangerous no-op-to-live config transition via Params Updates Would Change / Malicious Config Would Redirect in Keeper.UpdateChainConfig

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when a malicious config would redirect value or strand it, and cause `Keeper.UpdateChainConfig` to overwrite a different live record than the caller should be able to affect, so that it supply a config mutation that looks harmless at basic validation but becomes live and dangerous later, breaking the invariant that authority-gated writes must fully validate dangerous config transitions before commit, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/msg_update_chain_config.go::Keeper.UpdateChainConfig
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `Keeper.UpdateChainConfig` to overwrite a different live record than the caller should be able to affect, so it can supply a config mutation that looks harmless at basic validation but becomes live and dangerous later.
- Invariant to test: authority-gated writes must fully validate dangerous config transitions before commit
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
