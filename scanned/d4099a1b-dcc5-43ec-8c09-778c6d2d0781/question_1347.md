# Q1347: Target-id ambiguity applies one vote to the wrong process or migration via Tss Key Ids, Process / Message Is Directly Reachable in MsgUpdateParams.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with TSS key ids, process ids, or migration ids chosen to collide with existing state when the message is directly reachable over normal transaction submission, and cause `MsgUpdateParams.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it shape ids so a vote lands on a different record than intended, breaking the invariant that one TSS vote must map to exactly one intended process or migration, and resulting in Wrong TSS state causing direct loss or frozen funds?

## Target
- File/function: x/utss/types/msg_update_params.go::MsgUpdateParams.ValidateBasic
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: TSS key ids, process ids, or migration ids chosen to collide with existing state
- Exploit idea: Cause `MsgUpdateParams.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can shape ids so a vote lands on a different record than intended.
- Invariant to test: one TSS vote must map to exactly one intended process or migration
- Expected Immunefi impact: Wrong TSS state causing direct loss or frozen funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
