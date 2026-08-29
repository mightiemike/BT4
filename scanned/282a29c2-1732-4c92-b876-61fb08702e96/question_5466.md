# Q5466: mask-update via collateral-remove: pass a health check and then change, in the same transacti

## Question
Entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) while controlling the `price-feeds` buffers, can an unprivileged attacker make `mask-update` (mainnet/contracts/market/v0-market-vault.clar:94) pass a health check and then change, in the same transaction, the quantity it checked? `mask-update` sets or clears one bit, clearing only when the row reaches exactly zero, so the invariant that a value read from `index-cache` describes the vault as it is at the moment of use would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:94` -> `mask-update`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers
- Exploit idea: `mask-update` sets or clears one bit, clearing only when the row reaches exactly zero. Reach it through `collateral-remove` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the `price-feeds` buffers across its boundary values through `collateral-remove` in simnet and assert `mask-update` never returns a value that breaks the invariant.
