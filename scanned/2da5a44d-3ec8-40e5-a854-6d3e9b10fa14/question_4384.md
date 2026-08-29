# Q4384: is-healthy-with-mask via liquidate-redeem: reuse one price and index snapshot across a batch that mut

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the borrower targeted reach `is-healthy-with-mask` (mainnet/contracts/market/v0-4-market.clar:663) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it resolves an egroup for a caller-influenced mask and applies its LTV-BORROW, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:663` -> `is-healthy-with-mask`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the borrower targeted
- Exploit idea: `is-healthy-with-mask` resolves an egroup for a caller-influenced mask and applies its LTV-BORROW. Reach it through `liquidate-redeem` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `liquidate-redeem` with the borrower targeted, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
