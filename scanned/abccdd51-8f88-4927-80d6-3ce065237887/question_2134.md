# Q2134: Signer binding and declared target record can be split in TSS messages via Tss Key Ids, Process / Gasless Admission Can Make in MsgUpdateParams.GetSigners

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with TSS key ids, process ids, or migration ids chosen to collide with existing state when gasless admission can make repetition cheap, and cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so that it make one signer advance another record or process without the intended authority, breaking the invariant that TSS message authorization must bind signer and target record exactly, and resulting in Wrong TSS state leading to direct loss or freeze?

## Target
- File/function: x/utss/types/msg_update_params.go::MsgUpdateParams.GetSigners
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: TSS key ids, process ids, or migration ids chosen to collide with existing state
- Exploit idea: Cause `MsgUpdateParams.GetSigners` to derive the wrong effective signer or omit the real principal, so it can make one signer advance another record or process without the intended authority.
- Invariant to test: TSS message authorization must bind signer and target record exactly
- Expected Immunefi impact: Wrong TSS state leading to direct loss or freeze
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
