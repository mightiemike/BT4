# Q3424: mask-to-list-collateral via repay: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls the `ft` trait principal reach `mask-to-list-collateral` (mainnet/contracts/market/v0-4-market.clar:449) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it expands a mask to a list of ids over ITER-UINT-64, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:449` -> `mask-to-list-collateral`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `mask-to-list-collateral` expands a mask to a list of ids over ITER-UINT-64. Reach it through `repay` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `repay` with the `ft` trait principal, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
