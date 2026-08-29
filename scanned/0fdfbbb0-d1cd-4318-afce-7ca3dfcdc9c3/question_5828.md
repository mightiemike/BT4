# Q5828: accrue-user-debts via accrue: bind a balance before an accrual and assert against it aft

## Question
Does `accrue` (mainnet/contracts/vault/v0-vault-stx.clar:835) let an unprivileged attacker who controls the block time at which accrual is first triggered in a block reach `accrue-user-debts` (mainnet/contracts/market/v0-4-market.clar:259) in a state where it bind a balance before an accrual and assert against it after? Given that it folds accrual over the position's debt list only, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:259` -> `accrue-user-debts`
- Entrypoint: `accrue` (`mainnet/contracts/vault/v0-vault-stx.clar:835`), unprivileged and publicly callable
- Attacker controls: the block time at which accrual is first triggered in a block
- Exploit idea: `accrue-user-debts` folds accrual over the position's debt list only. Reach it through `accrue` and bind a balance before an accrual and assert against it after.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `accrue` twice with the block time at which accrual is first triggered in a block varied, and assert that the value `accrue-user-debts` returns is identical in both runs; a divergence confirms the finding.
