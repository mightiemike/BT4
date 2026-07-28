# Q3702: Signer binding and declared target record can be split in TSS messages via Gasless Tss Vote Messages / Message Is Directly Reachable in msgServer.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with gasless TSS vote messages submitted from an unfunded attacker account when the message is directly reachable over normal transaction submission, and cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it make one signer advance another record or process without the intended authority, breaking the invariant that TSS message authorization must bind signer and target record exactly, and resulting in Wrong TSS state leading to direct loss or freeze?

## Target
- File/function: x/utss/keeper/msg_server.go::msgServer.UpdateParams
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: gasless TSS vote messages submitted from an unfunded attacker account
- Exploit idea: Cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can make one signer advance another record or process without the intended authority.
- Invariant to test: TSS message authorization must bind signer and target record exactly
- Expected Immunefi impact: Wrong TSS state leading to direct loss or freeze
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
