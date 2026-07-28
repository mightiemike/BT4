# Q0156: Unprivileged TSS vote bypasses signer restrictions via Gasless Tss Vote Messages / Message Is Directly Reachable in msgServer.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with gasless TSS vote messages submitted from an unfunded attacker account when the message is directly reachable over normal transaction submission, and cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it make a gasless TSS vote from an unprivileged account count as though it came from an eligible UV, breaking the invariant that only eligible UVs should be able to advance TSS event or migration ballots, and resulting in Wrong TSS state leading to direct loss or permanent freezing of funds?

## Target
- File/function: x/utss/keeper/msg_server.go::msgServer.UpdateParams
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: gasless TSS vote messages submitted from an unfunded attacker account
- Exploit idea: Cause `msgServer.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can make a gasless TSS vote from an unprivileged account count as though it came from an eligible UV.
- Invariant to test: only eligible UVs should be able to advance TSS event or migration ballots
- Expected Immunefi impact: Wrong TSS state leading to direct loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
