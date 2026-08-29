# Q3728: oracle-price-legal via collateral-remove-redeem: leave the accrual clock stale so a later interval double-c

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls `amount` used for BOTH the collateral removal and the share redemption reach `oracle-price-legal` (mainnet/contracts/market/v0-4-market.clar:362) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it accepts any price strictly greater than zero, with no upper bound and no sanity band, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:362` -> `oracle-price-legal`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `amount` used for BOTH the collateral removal and the share redemption
- Exploit idea: `oracle-price-legal` accepts any price strictly greater than zero, with no upper bound and no sanity band. Reach it through `collateral-remove-redeem` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-remove-redeem` twice with `amount` used for BOTH the collateral removal and the share redemption varied, and assert that the value `oracle-price-legal` returns is identical in both runs; a divergence confirms the finding.
