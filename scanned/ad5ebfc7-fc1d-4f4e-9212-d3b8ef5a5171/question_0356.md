# Q0356: Unprivileged process initiation bypasses admin authority via Gasless Tss Vote Messages / Accepted Tss State Would in Keeper.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with gasless TSS vote messages submitted from an unfunded attacker account when accepted TSS state would affect live outbound signing or migration, and cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it start or alter TSS key lifecycle from a non-admin path, breaking the invariant that only the configured authority should initiate key processes or migrations, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/keeper/msg_update_params.go::Keeper.UpdateParams
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: gasless TSS vote messages submitted from an unfunded attacker account
- Exploit idea: Cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can start or alter TSS key lifecycle from a non-admin path.
- Invariant to test: only the configured authority should initiate key processes or migrations
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
