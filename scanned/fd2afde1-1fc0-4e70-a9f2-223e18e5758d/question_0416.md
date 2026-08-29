# Q0416: total-assets-preview via transfer: bind a balance before an accrual and assert against it aft

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls `amount` reach `total-assets-preview` (mainnet/contracts/vault/v0-vault-stx.clar:341) in a state where it bind a balance before an accrual and assert against it after? Given that it re-derives a FORWARD index inside calls that have already accrued, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:341` -> `total-assets-preview`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `total-assets-preview` re-derives a FORWARD index inside calls that have already accrued. Reach it through `transfer` and bind a balance before an accrual and assert against it after.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `transfer` twice with `amount` varied, and assert that the value `total-assets-preview` returns is identical in both runs; a divergence confirms the finding.
