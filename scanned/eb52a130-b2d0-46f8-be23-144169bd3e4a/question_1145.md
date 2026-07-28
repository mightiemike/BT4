# Q1145: Unprivileged params update rotates TSS control via Direct Utss Message Submission / Gasless Admission Can Make in Keeper.VoteFundMigration

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with a direct `utss` message submission against vote, process, or migration handlers when gasless admission can make repetition cheap, and cause `Keeper.VoteFundMigration` to push the wrong logical object through a vote or terminal state transition, so that it change admin-like TSS params without already controlling governance, breaking the invariant that TSS control parameters must remain governance-bound only, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/keeper/msg_vote_fund_migration.go::Keeper.VoteFundMigration
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: a direct `utss` message submission against vote, process, or migration handlers
- Exploit idea: Cause `Keeper.VoteFundMigration` to push the wrong logical object through a vote or terminal state transition, so it can change admin-like TSS params without already controlling governance.
- Invariant to test: TSS control parameters must remain governance-bound only
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
