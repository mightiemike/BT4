# Q0361: Unprivileged process initiation bypasses admin authority via Direct Utss Message Submission / Gasless Admission Can Make in MsgUpdateParams.GetSigners

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with a direct `utss` message submission against vote, process, or migration handlers when gasless admission can make repetition cheap, and cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so that it start or alter TSS key lifecycle from a non-admin path, breaking the invariant that only the configured authority should initiate key processes or migrations, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/types/msg_update_params.go::MsgUpdateParams.GetSigners
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: a direct `utss` message submission against vote, process, or migration handlers
- Exploit idea: Cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so it can start or alter TSS key lifecycle from a non-admin path.
- Invariant to test: only the configured authority should initiate key processes or migrations
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
