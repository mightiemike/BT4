# Q1176: mask-to-list-collateral via collateral-remove: reuse one price and index snapshot across a batch that mut

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls `amount` relative to the current collateral row (the removing-all branch) reach `mask-to-list-collateral` (mainnet/contracts/market/v0-4-market.clar:449) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it expands a mask to a list of ids over ITER-UINT-64, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:449` -> `mask-to-list-collateral`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: `amount` relative to the current collateral row (the removing-all branch)
- Exploit idea: `mask-to-list-collateral` expands a mask to a list of ids over ITER-UINT-64. Reach it through `collateral-remove` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `amount` relative to the current collateral row (the removing-all branch) across its boundary values through `collateral-remove` in simnet and assert `mask-to-list-collateral` never returns a value that breaks the invariant.
