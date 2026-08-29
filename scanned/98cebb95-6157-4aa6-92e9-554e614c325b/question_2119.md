# Q2119: insert via liquidate: absorb a sub-step failure into a fold flag and proceed on 

## Question
`insert` (mainnet/contracts/market/v0-market-vault.clar:159) rewrites the whole registry entry for a user id. Can an unprivileged caller of `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382), by choosing `min-collateral-expected`, use that to absorb a sub-step failure into a fold flag and proceed on partial state, violating the invariant that every second of elapsed time is charged exactly once, to one index, in one direction and producing direct theft of another user's collateral?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:159` -> `insert`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `min-collateral-expected`
- Exploit idea: `insert` rewrites the whole registry entry for a user id. Reach it through `liquidate` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate` with `min-collateral-expected`, then read `insert` state before and after in the same block and assert the two sides of the invariant are equal.
