# Q1531: vault-accrue via deposit: leave the accrual clock stale so a later interval double-c

## Question
`vault-accrue` (mainnet/contracts/market/v0-4-market.clar:189) dispatches accrual to one of six vaults by asset id. Can an unprivileged caller of `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763), by choosing `min-out`, use that to leave the accrual clock stale so a later interval double-counts elapsed time, violating the invariant that every second of elapsed time is charged exactly once, to one index, in one direction and producing direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:189` -> `vault-accrue`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: `min-out`
- Exploit idea: `vault-accrue` dispatches accrual to one of six vaults by asset id. Reach it through `deposit` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `deposit` with `min-out`, then read `vault-accrue` state before and after in the same block and assert the two sides of the invariant are equal.
