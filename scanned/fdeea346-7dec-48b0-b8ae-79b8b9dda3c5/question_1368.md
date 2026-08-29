# Q1368: filter-out-debt-asset via liquidate: strand value on the market contract when a later step of a

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `borrower`, any third-party principal reach `filter-out-debt-asset` (mainnet/contracts/market/v0-4-market.clar:633) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it rebuilds the debt list without one asset, under `as-max-len? ... u64`, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:633` -> `filter-out-debt-asset`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `borrower`, any third-party principal
- Exploit idea: `filter-out-debt-asset` rebuilds the debt list without one asset, under `as-max-len? ... u64`. Reach it through `liquidate` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `borrower`, any third-party principal across its boundary values through `liquidate` in simnet and assert `filter-out-debt-asset` never returns a value that breaks the invariant.
