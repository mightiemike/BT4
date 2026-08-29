# Q3789: get-account-scaled-debt via liquidate: strand value on the market contract when a later step of a

## Question
Can an unprivileged attacker entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382), controlling the `price-feeds` buffers and their ordering, drive `get-account-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:307) — which reads one scaled debt row — to strand value on the market contract when a later step of a composite call fails, breaking the invariant that the state a safety check approved is the state the money movement executes against, and cause temporary freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:307` -> `get-account-scaled-debt`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers and their ordering
- Exploit idea: `get-account-scaled-debt` reads one scaled debt row. Reach it through `liquidate` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Snapshot every state variable `get-account-scaled-debt` touches, run `liquidate` with the `price-feeds` buffers and their ordering, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
