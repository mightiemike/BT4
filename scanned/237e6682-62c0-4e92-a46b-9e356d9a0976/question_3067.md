# Q3067: Validation accepts a dangerous no-op-to-live config transition via Params Updates Would Change / Config Change Would Immediately in MsgRemoveTokenConfig.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the config change would immediately affect live user flows if accepted, and cause `MsgRemoveTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so that it supply a config mutation that looks harmless at basic validation but becomes live and dangerous later, breaking the invariant that authority-gated writes must fully validate dangerous config transitions before commit, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_remove_token_config.go::MsgRemoveTokenConfig.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `MsgRemoveTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so it can supply a config mutation that looks harmless at basic validation but becomes live and dangerous later.
- Invariant to test: authority-gated writes must fully validate dangerous config transitions before commit
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
