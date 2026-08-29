# Q2024: add-user-scaled-debt via liquidate: leave the accrual clock stale so a later interval double-c

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `collateral-receiver` reach `add-user-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:237) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it adds to the scaled debt row with a graceful u0 default, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:237` -> `add-user-scaled-debt`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `add-user-scaled-debt` adds to the scaled debt row with a graceful u0 default. Reach it through `liquidate` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `collateral-receiver` varied, and assert that the value `add-user-scaled-debt` returns is identical in both runs; a divergence confirms the finding.
