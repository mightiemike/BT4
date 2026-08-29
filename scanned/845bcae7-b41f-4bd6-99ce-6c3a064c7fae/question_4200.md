# Q4200: interpolate-rate via liquidate-redeem: leave the accrual clock stale so a later interval double-c

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the borrower targeted reach `interpolate-rate` (mainnet/contracts/vault/v0-vault-stx.clar:196) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it interpolates between packed u16 curve points, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:196` -> `interpolate-rate`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the borrower targeted
- Exploit idea: `interpolate-rate` interpolates between packed u16 curve points. Reach it through `liquidate-redeem` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the borrower targeted across its boundary values through `liquidate-redeem` in simnet and assert `interpolate-rate` never returns a value that breaks the invariant.
