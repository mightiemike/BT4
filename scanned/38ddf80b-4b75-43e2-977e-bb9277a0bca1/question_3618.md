# Q3618: get-bitmap via collateral-add: reuse one price and index snapshot across a batch that mut

## Question
Entering through `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) while controlling the three `price-feeds` buffers and their order, can an unprivileged attacker make `get-bitmap` (mainnet/contracts/registry/v0-assets.clar:145) reuse one price and index snapshot across a batch that mutates state between entries? `get-bitmap` returns the global enabled bitmap that every position read filters on, so the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives would fail, yielding temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/registry/v0-assets.clar:145` -> `get-bitmap`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the three `price-feeds` buffers and their order
- Exploit idea: `get-bitmap` returns the global enabled bitmap that every position read filters on. Reach it through `collateral-add` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the three `price-feeds` buffers and their order across its boundary values through `collateral-add` in simnet and assert `get-bitmap` never returns a value that breaks the invariant.
