# Q0456: debt-remove-scaled via liquidate: consume a cache entry after the vault it describes has alr

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls the `price-feeds` buffers and their ordering reach `debt-remove-scaled` (mainnet/contracts/market/v0-market-vault.clar:473) in a state where it consume a cache entry after the vault it describes has already moved? Given that it clears the debt bit only when the remaining scaled debt is exactly zero, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:473` -> `debt-remove-scaled`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers and their ordering
- Exploit idea: `debt-remove-scaled` clears the debt bit only when the remaining scaled debt is exactly zero. Reach it through `liquidate` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the `price-feeds` buffers and their ordering across its boundary values through `liquidate` in simnet and assert `debt-remove-scaled` never returns a value that breaks the invariant.
