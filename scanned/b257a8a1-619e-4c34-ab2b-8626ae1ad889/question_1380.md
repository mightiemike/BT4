# Q1380: accrue-user-collateral via liquidate-multi: pass a health check and then change, in the same transacti

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls the full batch list and its ordering reach `accrue-user-collateral` (mainnet/contracts/market/v0-4-market.clar:270) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it accrues only rows that `is-ztoken` recognises, skipping everything else, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:270` -> `accrue-user-collateral`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the full batch list and its ordering
- Exploit idea: `accrue-user-collateral` accrues only rows that `is-ztoken` recognises, skipping everything else. Reach it through `liquidate-multi` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the full batch list and its ordering across its boundary values through `liquidate-multi` in simnet and assert `accrue-user-collateral` never returns a value that breaks the invariant.
