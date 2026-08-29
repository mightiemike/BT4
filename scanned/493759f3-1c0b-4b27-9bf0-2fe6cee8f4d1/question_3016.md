# Q3016: write-feed via borrow: reuse one price and index snapshot across a batch that mut

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls `amount` reach `write-feed` (mainnet/contracts/market/v0-4-market.clar:129) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it applies one Pyth price-feed update and folds its status, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:129` -> `write-feed`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `write-feed` applies one Pyth price-feed update and folds its status. Reach it through `borrow` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `borrow` with `amount`, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
