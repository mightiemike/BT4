# Q1144: Unprivileged params update rotates TSS control via Gasless Tss Vote Messages / Accepted Tss State Would in Keeper.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with gasless TSS vote messages submitted from an unfunded attacker account when accepted TSS state would affect live outbound signing or migration, and cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it change admin-like TSS params without already controlling governance, breaking the invariant that TSS control parameters must remain governance-bound only, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/keeper/msg_update_params.go::Keeper.UpdateParams
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: gasless TSS vote messages submitted from an unfunded attacker account
- Exploit idea: Cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can change admin-like TSS params without already controlling governance.
- Invariant to test: TSS control parameters must remain governance-bound only
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
