# Q0079: write-feed via borrow: leave the accrual clock stale so a later interval double-c

## Question
`write-feed` (mainnet/contracts/market/v0-4-market.clar:129) applies one Pyth price-feed update and folds its status. Can an unprivileged caller of `borrow` (mainnet/contracts/market/v0-4-market.clar:1238), by choosing `amount`, use that to leave the accrual clock stale so a later interval double-counts elapsed time, violating the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives and producing theft of unclaimed yield?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:129` -> `write-feed`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `write-feed` applies one Pyth price-feed update and folds its status. Reach it through `borrow` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: In `local-testing/tests` on a local fork, drive `borrow` with `amount`, then read `write-feed` state before and after in the same block and assert the two sides of the invariant are equal.
