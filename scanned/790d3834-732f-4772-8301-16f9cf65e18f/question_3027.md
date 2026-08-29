# Q3027: add-user-scaled-debt via liquidate-multi: consume a cache entry after the vault it describes has alr

## Question
`add-user-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:237) adds to the scaled debt row with a graceful u0 default. Can an unprivileged caller of `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593), by choosing how many entries share one price snapshot (price-feeds is passed as none), use that to consume a cache entry after the vault it describes has already moved, violating the invariant that every second of elapsed time is charged exactly once, to one index, in one direction and producing permanent freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:237` -> `add-user-scaled-debt`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: how many entries share one price snapshot (price-feeds is passed as none)
- Exploit idea: `add-user-scaled-debt` adds to the scaled debt row with a graceful u0 default. Reach it through `liquidate-multi` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Snapshot every state variable `add-user-scaled-debt` touches, run `liquidate-multi` with how many entries share one price snapshot (price-feeds is passed as none), recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
