# Q2264: accrue-user-debts via liquidate: reuse one price and index snapshot across a batch that mut

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `collateral-receiver` reach `accrue-user-debts` (mainnet/contracts/market/v0-4-market.clar:259) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it folds accrual over the position's debt list only, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:259` -> `accrue-user-debts`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `accrue-user-debts` folds accrual over the position's debt list only. Reach it through `liquidate` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `collateral-receiver` varied, and assert that the value `accrue-user-debts` returns is identical in both runs; a divergence confirms the finding.
