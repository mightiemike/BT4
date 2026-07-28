# Q2726: Unprivileged params update rotates TSS control via Tss Key Ids, Process / Attacker Does Not Already in MsgUpdateParams.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with TSS key ids, process ids, or migration ids chosen to collide with existing state when the attacker does not already control a UV, admin, or governance key, and cause `MsgUpdateParams.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it change admin-like TSS params without already controlling governance, breaking the invariant that TSS control parameters must remain governance-bound only, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/types/msg_update_params.go::MsgUpdateParams.ValidateBasic
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: TSS key ids, process ids, or migration ids chosen to collide with existing state
- Exploit idea: Cause `MsgUpdateParams.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can change admin-like TSS params without already controlling governance.
- Invariant to test: TSS control parameters must remain governance-bound only
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
