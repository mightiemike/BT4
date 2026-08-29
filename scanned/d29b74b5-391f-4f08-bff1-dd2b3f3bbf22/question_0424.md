# Q0424: calc-index-next via liquidate: reuse one price and index snapshot across a batch that mut

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `collateral-receiver` reach `calc-index-next` (mainnet/contracts/vault/v0-vault-stx.clar:183) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it applies a multiplier to the current index, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:183` -> `calc-index-next`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `calc-index-next` applies a multiplier to the current index. Reach it through `liquidate` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `liquidate` with `collateral-receiver`, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
