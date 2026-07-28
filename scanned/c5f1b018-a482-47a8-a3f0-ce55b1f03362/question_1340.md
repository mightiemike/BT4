# Q1340: Target-id ambiguity applies one vote to the wrong process or migration via Gasless Tss Vote Messages / Attacker Does Not Already in msgServer.VoteTssKeyProcess

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with gasless TSS vote messages submitted from an unfunded attacker account when the attacker does not already control a UV, admin, or governance key, and cause `msgServer.VoteTssKeyProcess` to push the wrong logical object through a vote or terminal state transition, so that it shape ids so a vote lands on a different record than intended, breaking the invariant that one TSS vote must map to exactly one intended process or migration, and resulting in Wrong TSS state causing direct loss or frozen funds?

## Target
- File/function: x/utss/keeper/msg_server.go::msgServer.VoteTssKeyProcess
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: gasless TSS vote messages submitted from an unfunded attacker account
- Exploit idea: Cause `msgServer.VoteTssKeyProcess` to push the wrong logical object through a vote or terminal state transition, so it can shape ids so a vote lands on a different record than intended.
- Invariant to test: one TSS vote must map to exactly one intended process or migration
- Expected Immunefi impact: Wrong TSS state causing direct loss or frozen funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
