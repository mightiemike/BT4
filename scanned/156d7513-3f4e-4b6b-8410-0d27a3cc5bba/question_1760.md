# Q1760: interest-rate via redeem: strand value on the market contract when a later step of a

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls `amount` of shares burned reach `interest-rate` (mainnet/contracts/vault/v0-vault-stx.clar:371) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it interpolates the packed curve at the current utilization, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:371` -> `interest-rate`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `amount` of shares burned
- Exploit idea: `interest-rate` interpolates the packed curve at the current utilization. Reach it through `redeem` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `redeem` twice with `amount` of shares burned varied, and assert that the value `interest-rate` returns is identical in both runs; a divergence confirms the finding.
