# Q1069: add-user-scaled-debt via liquidate-multi: leave the accrual clock stale so a later interval double-c

## Question
Can an unprivileged attacker entering through `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593), controlling how many entries share one price snapshot (price-feeds is passed as none), drive `add-user-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:237) — which adds to the scaled debt row with a graceful u0 default — to leave the accrual clock stale so a later interval double-counts elapsed time, breaking the invariant that a value read from `index-cache` describes the vault as it is at the moment of use, and cause theft of unclaimed yield?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:237` -> `add-user-scaled-debt`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: how many entries share one price snapshot (price-feeds is passed as none)
- Exploit idea: `add-user-scaled-debt` adds to the scaled debt row with a graceful u0 default. Reach it through `liquidate-multi` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate-multi` with how many entries share one price snapshot (price-feeds is passed as none), then read `add-user-scaled-debt` state before and after in the same block and assert the two sides of the invariant are equal.
