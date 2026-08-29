# Q4060: get-cached-indexes via deposit: leave the accrual clock stale so a later interval double-c

## Question
Does `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763) let an unprivileged attacker who controls the vault's supply and asset state at the moment of the call reach `get-cached-indexes` (mainnet/contracts/market/v0-4-market.clar:944) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is direct theft of user funds at rest or in motion.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:944` -> `get-cached-indexes`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: the vault's supply and asset state at the moment of the call
- Exploit idea: `get-cached-indexes` reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on. Reach it through `deposit` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: Set up the position in simnet, call `deposit` with the vault's supply and asset state at the moment of the call, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
