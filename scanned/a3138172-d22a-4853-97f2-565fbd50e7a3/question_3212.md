# Q3212: interest-rate via collateral-remove: reuse one price and index snapshot across a batch that mut

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls the `price-feeds` buffers reach `interest-rate` (mainnet/contracts/vault/v0-vault-stx.clar:371) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it interpolates the packed curve at the current utilization, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:371` -> `interest-rate`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers
- Exploit idea: `interest-rate` interpolates the packed curve at the current utilization. Reach it through `collateral-remove` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-remove` twice with the `price-feeds` buffers varied, and assert that the value `interest-rate` returns is identical in both runs; a divergence confirms the finding.
