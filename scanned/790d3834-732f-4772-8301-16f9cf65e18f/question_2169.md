# Q2169: accrue-collateral-asset via repay: strand value on the market contract when a later step of a

## Question
Can an unprivileged attacker entering through `repay` (mainnet/contracts/market/v0-4-market.clar:1316), controlling the `ft` trait principal, drive `accrue-collateral-asset` (mainnet/contracts/market/v0-4-market.clar:273) — which maps a ztoken id to a vault id through a chain of `is-eq` tests that falls through to the u100 sentinel — to strand value on the market contract when a later step of a composite call fails, breaking the invariant that every second of elapsed time is charged exactly once, to one index, in one direction, and cause temporary freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:273` -> `accrue-collateral-asset`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `accrue-collateral-asset` maps a ztoken id to a vault id through a chain of `is-eq` tests that falls through to the u100 sentinel. Reach it through `repay` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Snapshot every state variable `accrue-collateral-asset` touches, run `repay` with the `ft` trait principal, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
