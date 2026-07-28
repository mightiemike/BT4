# Q2914: Target-id ambiguity applies one vote to the wrong process or migration via Tss Key Ids, Process / Gasless Admission Can Make in msgServer.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with TSS key ids, process ids, or migration ids chosen to collide with existing state when gasless admission can make repetition cheap, and cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it shape ids so a vote lands on a different record than intended, breaking the invariant that one TSS vote must map to exactly one intended process or migration, and resulting in Wrong TSS state causing direct loss or frozen funds?

## Target
- File/function: x/utss/keeper/msg_server.go::msgServer.UpdateParams
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: TSS key ids, process ids, or migration ids chosen to collide with existing state
- Exploit idea: Cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can shape ids so a vote lands on a different record than intended.
- Invariant to test: one TSS vote must map to exactly one intended process or migration
- Expected Immunefi impact: Wrong TSS state causing direct loss or frozen funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
