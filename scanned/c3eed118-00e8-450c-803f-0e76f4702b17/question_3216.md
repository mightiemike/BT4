# Q3216: get-egroup via liquidate: consume a cache entry after the vault it describes has alr

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `min-collateral-expected` reach `get-egroup` (mainnet/contracts/market/v0-4-market.clar:460) in a state where it consume a cache entry after the vault it describes has already moved? Given that it resolves the efficiency group for a mask and is unwrapped with `try!` on every health path, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:460` -> `get-egroup`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `min-collateral-expected`
- Exploit idea: `get-egroup` resolves the efficiency group for a mask and is unwrapped with `try!` on every health path. Reach it through `liquidate` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `min-collateral-expected` across its boundary values through `liquidate` in simnet and assert `get-egroup` never returns a value that breaks the invariant.
