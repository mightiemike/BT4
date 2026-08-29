# Q0828: refresh via collateral-remove-redeem: leave the accrual clock stale so a later interval double-c

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls `receiver` for the underlying leg reach `refresh` (mainnet/contracts/market/v0-market-vault.clar:171) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:171` -> `refresh`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `receiver` for the underlying leg
- Exploit idea: `refresh` rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write. Reach it through `collateral-remove-redeem` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `receiver` for the underlying leg across its boundary values through `collateral-remove-redeem` in simnet and assert `refresh` never returns a value that breaks the invariant.
