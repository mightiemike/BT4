# Q1884: get-cached-indexes via supply-collateral-add: pass a health check and then change, in the same transacti

## Question
Does `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) let an unprivileged attacker who controls the position state the final collateral-add is validated against reach `get-cached-indexes` (mainnet/contracts/market/v0-4-market.clar:944) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:944` -> `get-cached-indexes`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: the position state the final collateral-add is validated against
- Exploit idea: `get-cached-indexes` reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on. Reach it through `supply-collateral-add` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the position state the final collateral-add is validated against across its boundary values through `supply-collateral-add` in simnet and assert `get-cached-indexes` never returns a value that breaks the invariant.
