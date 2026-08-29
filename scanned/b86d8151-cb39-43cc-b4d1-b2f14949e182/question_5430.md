# Q5430: iter-find-superset via collateral-add: reuse one price and index snapshot across a batch that mut

## Question
Entering through `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) while controlling whether this asset is already collateral (the is-new-collateral branch), can an unprivileged attacker make `iter-find-superset` (mainnet/contracts/registry/v0-egroup.clar:267) reuse one price and index snapshot across a batch that mutates state between entries? `iter-find-superset` short-circuits on the first superset match, so the invariant that the state a safety check approved is the state the money movement executes against would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:267` -> `iter-find-superset`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: whether this asset is already collateral (the is-new-collateral branch)
- Exploit idea: `iter-find-superset` short-circuits on the first superset match. Reach it through `collateral-add` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz whether this asset is already collateral (the is-new-collateral branch) across its boundary values through `collateral-add` in simnet and assert `iter-find-superset` never returns a value that breaks the invariant.
