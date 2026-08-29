# Q3739: subset via collateral-remove-redeem: absorb a sub-step failure into a fold flag and proceed on 

## Question
`subset` (mainnet/contracts/market/v0-market-vault.clar:100) tests bitmask containment. Can an unprivileged caller of `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211), by choosing `min-underlying`, use that to absorb a sub-step failure into a fold flag and proceed on partial state, violating the invariant that the state a safety check approved is the state the money movement executes against and producing direct theft of another user's collateral?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:100` -> `subset`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `min-underlying`
- Exploit idea: `subset` tests bitmask containment. Reach it through `collateral-remove-redeem` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: In `local-testing/tests` on a local fork, drive `collateral-remove-redeem` with `min-underlying`, then read `subset` state before and after in the same block and assert the two sides of the invariant are equal.
