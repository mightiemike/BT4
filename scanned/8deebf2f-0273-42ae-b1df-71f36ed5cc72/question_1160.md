# Q1160: interpolate-rate via borrow: leave the accrual clock stale so a later interval double-c

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the `ft` trait principal reach `interpolate-rate` (mainnet/contracts/vault/v0-vault-stx.clar:196) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it interpolates between packed u16 curve points, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:196` -> `interpolate-rate`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `interpolate-rate` interpolates between packed u16 curve points. Reach it through `borrow` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with the `ft` trait principal varied, and assert that the value `interpolate-rate` returns is identical in both runs; a divergence confirms the finding.
