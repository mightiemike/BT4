# Q1149: interpolate-rate via collateral-remove: pass a health check and then change, in the same transacti

## Question
Can an unprivileged attacker entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107), controlling whether the position has any enabled debt row (the has-debt branch), drive `interpolate-rate` (mainnet/contracts/vault/v0-vault-stx.clar:196) — which interpolates between packed u16 curve points — to pass a health check and then change, in the same transaction, the quantity it checked, breaking the invariant that a value read from `index-cache` describes the vault as it is at the moment of use, and cause permanent freezing of a position that can never be closed?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:196` -> `interpolate-rate`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: whether the position has any enabled debt row (the has-debt branch)
- Exploit idea: `interpolate-rate` interpolates between packed u16 curve points. Reach it through `collateral-remove` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Snapshot every state variable `interpolate-rate` touches, run `collateral-remove` with whether the position has any enabled debt row (the has-debt branch), recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
