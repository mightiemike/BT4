# Q0799: insert via repay: consume a cache entry after the vault it describes has alr

## Question
`insert` (mainnet/contracts/market/v0-market-vault.clar:159) rewrites the whole registry entry for a user id. Can an unprivileged caller of `repay` (mainnet/contracts/market/v0-4-market.clar:1316), by choosing the `ft` trait principal, use that to consume a cache entry after the vault it describes has already moved, violating the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives and producing theft of unclaimed yield?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:159` -> `insert`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `insert` rewrites the whole registry entry for a user id. Reach it through `repay` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: In `local-testing/tests` on a local fork, drive `repay` with the `ft` trait principal, then read `insert` state before and after in the same block and assert the two sides of the invariant are equal.
