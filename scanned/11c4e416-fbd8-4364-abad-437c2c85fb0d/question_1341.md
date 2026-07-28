# Q1341: Target-id ambiguity applies one vote to the wrong process or migration via Direct Utss Message Submission / Message Is Directly Reachable in Keeper.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with a direct `utss` message submission against vote, process, or migration handlers when the message is directly reachable over normal transaction submission, and cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it shape ids so a vote lands on a different record than intended, breaking the invariant that one TSS vote must map to exactly one intended process or migration, and resulting in Wrong TSS state causing direct loss or frozen funds?

## Target
- File/function: x/utss/keeper/msg_update_params.go::Keeper.UpdateParams
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: a direct `utss` message submission against vote, process, or migration handlers
- Exploit idea: Cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can shape ids so a vote lands on a different record than intended.
- Invariant to test: one TSS vote must map to exactly one intended process or migration
- Expected Immunefi impact: Wrong TSS state causing direct loss or frozen funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
