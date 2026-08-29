# Q5736: get-egroup via supply-collateral-add: leave the accrual clock stale so a later interval double-c

## Question
Does `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) let an unprivileged attacker who controls `amount` reach `get-egroup` (mainnet/contracts/market/v0-4-market.clar:460) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it resolves the efficiency group for a mask and is unwrapped with `try!` on every health path, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:460` -> `get-egroup`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `get-egroup` resolves the efficiency group for a mask and is unwrapped with `try!` on every health path. Reach it through `supply-collateral-add` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `amount` across its boundary values through `supply-collateral-add` in simnet and assert `get-egroup` never returns a value that breaks the invariant.
