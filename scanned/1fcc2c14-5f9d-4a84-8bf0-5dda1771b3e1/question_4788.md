# Q4788: user-safe-mask via supply-collateral-add: leave the accrual clock stale so a later interval double-c

## Question
Does `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) let an unprivileged attacker who controls `min-shares` (the only slippage bound on the deposit leg) reach `user-safe-mask` (mainnet/contracts/market/v0-4-market.clar:428) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it ANDs the user's collateral bits against the enabled bitmap but keeps ALL debt bits unfiltered, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:428` -> `user-safe-mask`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: `min-shares` (the only slippage bound on the deposit leg)
- Exploit idea: `user-safe-mask` ANDs the user's collateral bits against the enabled bitmap but keeps ALL debt bits unfiltered. Reach it through `supply-collateral-add` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `min-shares` (the only slippage bound on the deposit leg) across its boundary values through `supply-collateral-add` in simnet and assert `user-safe-mask` never returns a value that breaks the invariant.
