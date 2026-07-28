# Q1148: Unprivileged params update rotates TSS control via Gasless Tss Vote Messages / Accepted Tss State Would in MsgVoteTssKeyProcess.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with gasless TSS vote messages submitted from an unfunded attacker account when accepted TSS state would affect live outbound signing or migration, and cause `MsgVoteTssKeyProcess.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it change admin-like TSS params without already controlling governance, breaking the invariant that TSS control parameters must remain governance-bound only, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/types/msg_tss_key_process.go::MsgVoteTssKeyProcess.ValidateBasic
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: gasless TSS vote messages submitted from an unfunded attacker account
- Exploit idea: Cause `MsgVoteTssKeyProcess.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can change admin-like TSS params without already controlling governance.
- Invariant to test: TSS control parameters must remain governance-bound only
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
