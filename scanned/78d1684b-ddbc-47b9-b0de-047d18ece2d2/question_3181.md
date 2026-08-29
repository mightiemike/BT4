# Q3181: status via liquidate: consume a cache entry after the vault it describes has alr

## Question
Can an unprivileged attacker entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382), controlling `collateral-receiver`, drive `status` (mainnet/contracts/registry/v0-assets.clar:115) — which derives `collateral` and `debt` flags from bit tests against whatever mask it was handed — to consume a cache entry after the vault it describes has already moved, breaking the invariant that a failed sub-step aborts the transaction or is explicitly compensated, and cause direct theft of another user's collateral?

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:115` -> `status`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `status` derives `collateral` and `debt` flags from bit tests against whatever mask it was handed. Reach it through `liquidate` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate` with `collateral-receiver`, then read `status` state before and after in the same block and assert the two sides of the invariant are equal.
