# Q1680: Unprivileged chain-config creation bypasses authority via Direct Submission Of Admin- / Message Is Directly User-Submittable in msgServer.UpdateTokenConfig

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a direct submission of an admin- or gov-gated `uregistry` message when the message is directly user-submittable over normal transaction channels, and cause `msgServer.UpdateTokenConfig` to overwrite a different live record than the caller should be able to affect, so that it make an admin-gated registry write succeed from an unprivileged account, breaking the invariant that only the configured registry authority should mutate chain configuration, and resulting in Direct theft/loss or permanent freezing of funds by malicious reconfiguration?

## Target
- File/function: x/uregistry/keeper/msg_server.go::msgServer.UpdateTokenConfig
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a direct submission of an admin- or gov-gated `uregistry` message
- Exploit idea: Cause `msgServer.UpdateTokenConfig` to overwrite a different live record than the caller should be able to affect, so it can make an admin-gated registry write succeed from an unprivileged account.
- Invariant to test: only the configured registry authority should mutate chain configuration
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds by malicious reconfiguration
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
