# Q1940: accrue-user-debts via repay: pass a health check and then change, in the same transacti

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls `amount`, including far above the real debt (the capping path) reach `accrue-user-debts` (mainnet/contracts/market/v0-4-market.clar:259) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it folds accrual over the position's debt list only, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:259` -> `accrue-user-debts`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: `amount`, including far above the real debt (the capping path)
- Exploit idea: `accrue-user-debts` folds accrual over the position's debt list only. Reach it through `repay` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `repay` twice with `amount`, including far above the real debt (the capping path) varied, and assert that the value `accrue-user-debts` returns is identical in both runs; a divergence confirms the finding.
