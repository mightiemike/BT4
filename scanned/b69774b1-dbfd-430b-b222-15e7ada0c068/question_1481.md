# Q1481: Validation accepts a dangerous no-op-to-live config transition via Direct Submission Of Admin- / Message Is Directly User-Submittable in msgServer.UpdateChainConfig

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a direct submission of an admin- or gov-gated `uregistry` message when the message is directly user-submittable over normal transaction channels, and cause `msgServer.UpdateChainConfig` to overwrite a different live record than the caller should be able to affect, so that it supply a config mutation that looks harmless at basic validation but becomes live and dangerous later, breaking the invariant that authority-gated writes must fully validate dangerous config transitions before commit, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/keeper/msg_server.go::msgServer.UpdateChainConfig
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a direct submission of an admin- or gov-gated `uregistry` message
- Exploit idea: Cause `msgServer.UpdateChainConfig` to overwrite a different live record than the caller should be able to affect, so it can supply a config mutation that looks harmless at basic validation but becomes live and dangerous later.
- Invariant to test: authority-gated writes must fully validate dangerous config transitions before commit
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
