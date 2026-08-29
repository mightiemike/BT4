# Q5120: resolve-interpolation-points via call-ststx-ratio: leave the accrual clock stale so a later interval double-c

## Question
Does `call-ststx-ratio` (mainnet/contracts/market/v0-4-market.clar:1015) let an unprivileged attacker who controls the block and transaction position at which the external ratio is fetched reach `resolve-interpolation-points` (mainnet/contracts/vault/v0-vault-stx.clar:205) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it selects the bracketing curve points for a utilization, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:205` -> `resolve-interpolation-points`
- Entrypoint: `call-ststx-ratio` (`mainnet/contracts/market/v0-4-market.clar:1015`), unprivileged and publicly callable
- Attacker controls: the block and transaction position at which the external ratio is fetched
- Exploit idea: `resolve-interpolation-points` selects the bracketing curve points for a utilization. Reach it through `call-ststx-ratio` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `call-ststx-ratio` twice with the block and transaction position at which the external ratio is fetched varied, and assert that the value `resolve-interpolation-points` returns is identical in both runs; a divergence confirms the finding.
