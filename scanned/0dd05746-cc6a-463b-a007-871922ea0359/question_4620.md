# Q4620: refresh via liquidate-multi: leave the accrual clock stale so a later interval double-c

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls the trait principals supplied per entry reach `refresh` (mainnet/contracts/market/v0-market-vault.clar:171) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:171` -> `refresh`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the trait principals supplied per entry
- Exploit idea: `refresh` rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write. Reach it through `liquidate-multi` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the trait principals supplied per entry across its boundary values through `liquidate-multi` in simnet and assert `refresh` never returns a value that breaks the invariant.
