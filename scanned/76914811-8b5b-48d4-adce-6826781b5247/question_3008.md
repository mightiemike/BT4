# Q3008: interest-rate via liquidate: bind a balance before an accrual and assert against it aft

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls which collateral and debt asset pair is targeted reach `interest-rate` (mainnet/contracts/vault/v0-vault-stx.clar:371) in a state where it bind a balance before an accrual and assert against it after? Given that it interpolates the packed curve at the current utilization, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:371` -> `interest-rate`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: which collateral and debt asset pair is targeted
- Exploit idea: `interest-rate` interpolates the packed curve at the current utilization. Reach it through `liquidate` and bind a balance before an accrual and assert against it after.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with which collateral and debt asset pair is targeted varied, and assert that the value `interest-rate` returns is identical in both runs; a divergence confirms the finding.
