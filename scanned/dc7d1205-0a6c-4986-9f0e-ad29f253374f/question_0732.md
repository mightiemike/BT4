# Q0732: mask-to-list-collateral via collateral-remove-redeem: reuse one price and index snapshot across a batch that mut

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls `min-underlying` reach `mask-to-list-collateral` (mainnet/contracts/market/v0-4-market.clar:449) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it expands a mask to a list of ids over ITER-UINT-64, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:449` -> `mask-to-list-collateral`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `min-underlying`
- Exploit idea: `mask-to-list-collateral` expands a mask to a list of ids over ITER-UINT-64. Reach it through `collateral-remove-redeem` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `min-underlying` across its boundary values through `collateral-remove-redeem` in simnet and assert `mask-to-list-collateral` never returns a value that breaks the invariant.
