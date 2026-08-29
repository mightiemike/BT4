# Q1250: get-bitmap via liquidate: consume a cache entry after the vault it describes has alr

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling `min-collateral-expected`, can an unprivileged attacker make `get-bitmap` (mainnet/contracts/registry/v0-assets.clar:145) consume a cache entry after the vault it describes has already moved? `get-bitmap` returns the global enabled bitmap that every position read filters on, so the invariant that a failed sub-step aborts the transaction or is explicitly compensated would fail, yielding permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:145` -> `get-bitmap`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `min-collateral-expected`
- Exploit idea: `get-bitmap` returns the global enabled bitmap that every position read filters on. Reach it through `liquidate` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `min-collateral-expected` varied, and assert that the value `get-bitmap` returns is identical in both runs; a divergence confirms the finding.
