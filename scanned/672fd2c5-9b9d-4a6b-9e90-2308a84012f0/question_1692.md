# Q1692: Unprivileged chain-config creation bypasses authority via Direct Submission Of Admin- / Message Is Directly User-Submittable in MsgUpdateParams.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a direct submission of an admin- or gov-gated `uregistry` message when the message is directly user-submittable over normal transaction channels, and cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so that it make an admin-gated registry write succeed from an unprivileged account, breaking the invariant that only the configured registry authority should mutate chain configuration, and resulting in Direct theft/loss or permanent freezing of funds by malicious reconfiguration?

## Target
- File/function: x/uregistry/types/msg_update_params.go::MsgUpdateParams.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a direct submission of an admin- or gov-gated `uregistry` message
- Exploit idea: Cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so it can make an admin-gated registry write succeed from an unprivileged account.
- Invariant to test: only the configured registry authority should mutate chain configuration
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds by malicious reconfiguration
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
