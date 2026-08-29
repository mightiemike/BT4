# Q1528: iter-lookup-debt via collateral-add: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls whether this asset is already collateral (the is-new-collateral branch) reach `iter-lookup-debt` (mainnet/contracts/market/v0-market-vault.clar:218) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it skips rows failing `relevant`, so a disabled asset's DEBT vanishes from the returned position, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is theft of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:218` -> `iter-lookup-debt`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: whether this asset is already collateral (the is-new-collateral branch)
- Exploit idea: `iter-lookup-debt` skips rows failing `relevant`, so a disabled asset's DEBT vanishes from the returned position. Reach it through `collateral-add` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: Set up the position in simnet, call `collateral-add` with whether this asset is already collateral (the is-new-collateral branch), and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
