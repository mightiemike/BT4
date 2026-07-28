# Q3708: Signer binding and declared target record can be split in TSS messages via Signer Fields, Authz Wrapping, / Message Is Directly Reachable in MsgInitiateTssKeyProcess.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with signer fields, authz wrapping, or message ids that would matter if authority checks fail when the message is directly reachable over normal transaction submission, and cause `MsgInitiateTssKeyProcess.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it make one signer advance another record or process without the intended authority, breaking the invariant that TSS message authorization must bind signer and target record exactly, and resulting in Wrong TSS state leading to direct loss or freeze?

## Target
- File/function: x/utss/types/msg_tss_key_process.go::MsgInitiateTssKeyProcess.ValidateBasic
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: signer fields, authz wrapping, or message ids that would matter if authority checks fail
- Exploit idea: Cause `MsgInitiateTssKeyProcess.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can make one signer advance another record or process without the intended authority.
- Invariant to test: TSS message authorization must bind signer and target record exactly
- Expected Immunefi impact: Wrong TSS state leading to direct loss or freeze
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
