# Q4740: remove-user-scaled-debt via liquidate: bind a balance before an accrual and assert against it aft

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `collateral-receiver` reach `remove-user-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:244) in a state where it bind a balance before an accrual and assert against it after? Given that it deletes the row only on an exact zero, otherwise leaving a residue that keeps the mask bit set, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:244` -> `remove-user-scaled-debt`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `remove-user-scaled-debt` deletes the row only on an exact zero, otherwise leaving a residue that keeps the mask bit set. Reach it through `liquidate` and bind a balance before an accrual and assert against it after.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `collateral-receiver` across its boundary values through `liquidate` in simnet and assert `remove-user-scaled-debt` never returns a value that breaks the invariant.
