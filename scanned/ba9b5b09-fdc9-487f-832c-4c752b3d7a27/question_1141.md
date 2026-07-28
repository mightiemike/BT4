# Q1141: Unprivileged params update rotates TSS control via Direct Utss Message Submission / Gasless Admission Can Make in msgServer.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with a direct `utss` message submission against vote, process, or migration handlers when gasless admission can make repetition cheap, and cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it change admin-like TSS params without already controlling governance, breaking the invariant that TSS control parameters must remain governance-bound only, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/keeper/msg_server.go::msgServer.UpdateParams
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: a direct `utss` message submission against vote, process, or migration handlers
- Exploit idea: Cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can change admin-like TSS params without already controlling governance.
- Invariant to test: TSS control parameters must remain governance-bound only
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
