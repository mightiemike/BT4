# Q1890: get-full-position via borrow: reuse one price and index snapshot across a batch that mut

## Question
Entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) while controlling `amount`, can an unprivileged attacker make `get-full-position` (mainnet/contracts/market/v0-4-market.clar:470) reuse one price and index snapshot across a batch that mutates state between entries? `get-full-position` returns all collateral rows regardless of the enabled bitmap, so the invariant that a failed sub-step aborts the transaction or is explicitly compensated would fail, yielding temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:470` -> `get-full-position`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `get-full-position` returns all collateral rows regardless of the enabled bitmap. Reach it through `borrow` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `amount` across its boundary values through `borrow` in simnet and assert `get-full-position` never returns a value that breaks the invariant.
