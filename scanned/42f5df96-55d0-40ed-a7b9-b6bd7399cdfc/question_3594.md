# Q3594: get-bitmap via collateral-remove: consume a cache entry after the vault it describes has alr

## Question
Entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) while controlling the `ft` trait principal, can an unprivileged attacker make `get-bitmap` (mainnet/contracts/registry/v0-assets.clar:145) consume a cache entry after the vault it describes has already moved? `get-bitmap` returns the global enabled bitmap that every position read filters on, so the invariant that the state a safety check approved is the state the money movement executes against would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:145` -> `get-bitmap`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `get-bitmap` returns the global enabled bitmap that every position read filters on. Reach it through `collateral-remove` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the `ft` trait principal across its boundary values through `collateral-remove` in simnet and assert `get-bitmap` never returns a value that breaks the invariant.
