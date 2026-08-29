# Q2740: relevant via collateral-add: reuse one price and index snapshot across a batch that mut

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls call ordering within the block reach `relevant` (mainnet/contracts/market/v0-market-vault.clar:175) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it drops any position row whose bit is not present in the enabled mask, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is direct theft of another user's collateral.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:175` -> `relevant`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: call ordering within the block
- Exploit idea: `relevant` drops any position row whose bit is not present in the enabled mask. Reach it through `collateral-add` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - direct theft of another user's collateral
- Fast validation: Set up the position in simnet, call `collateral-add` with call ordering within the block, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
