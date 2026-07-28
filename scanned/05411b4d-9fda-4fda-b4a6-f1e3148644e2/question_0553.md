# Q0553: Signer binding and declared target record can be split in TSS messages via Direct Utss Message Submission / Message Is Directly Reachable in Keeper.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with a direct `utss` message submission against vote, process, or migration handlers when the message is directly reachable over normal transaction submission, and cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it make one signer advance another record or process without the intended authority, breaking the invariant that TSS message authorization must bind signer and target record exactly, and resulting in Wrong TSS state leading to direct loss or freeze?

## Target
- File/function: x/utss/keeper/msg_update_params.go::Keeper.UpdateParams
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: a direct `utss` message submission against vote, process, or migration handlers
- Exploit idea: Cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can make one signer advance another record or process without the intended authority.
- Invariant to test: TSS message authorization must bind signer and target record exactly
- Expected Immunefi impact: Wrong TSS state leading to direct loss or freeze
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
