# Q2268: remove-user-collateral via repay: consume a cache entry after the vault it describes has alr

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls `on-behalf-of`, naming any third-party principal reach `remove-user-collateral` (mainnet/contracts/market/v0-market-vault.clar:205) in a state where it consume a cache entry after the vault it describes has already moved? Given that it asserts sufficiency then `map-delete`s only on an exact zero, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:205` -> `remove-user-collateral`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: `on-behalf-of`, naming any third-party principal
- Exploit idea: `remove-user-collateral` asserts sufficiency then `map-delete`s only on an exact zero. Reach it through `repay` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `on-behalf-of`, naming any third-party principal across its boundary values through `repay` in simnet and assert `remove-user-collateral` never returns a value that breaks the invariant.
