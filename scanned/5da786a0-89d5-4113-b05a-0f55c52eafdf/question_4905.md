# Q4905: find-and-resolve-asset-value via liquidate-multi: reuse one price and index snapshot across a batch that mut

## Question
Can an unprivileged attacker entering through `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593), controlling how many entries share one price snapshot (price-feeds is passed as none), drive `find-and-resolve-asset-value` (mainnet/contracts/market/v0-4-market.clar:668) — which reuses an already-resolved price from the asset list and returns u0 when the asset is not found — to reuse one price and index snapshot across a batch that mutates state between entries, breaking the invariant that the state a safety check approved is the state the money movement executes against, and cause permanent freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:668` -> `find-and-resolve-asset-value`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: how many entries share one price snapshot (price-feeds is passed as none)
- Exploit idea: `find-and-resolve-asset-value` reuses an already-resolved price from the asset list and returns u0 when the asset is not found. Reach it through `liquidate-multi` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Snapshot every state variable `find-and-resolve-asset-value` touches, run `liquidate-multi` with how many entries share one price snapshot (price-feeds is passed as none), recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
