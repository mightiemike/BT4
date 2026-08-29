# Q1364: debt-preview via accrue: bind a balance before an accrual and assert against it aft

## Question
Does `accrue` (mainnet/contracts/vault/v0-vault-stx.clar:835) let an unprivileged attacker who controls whether an earlier call in the same block already advanced last-update reach `debt-preview` (mainnet/contracts/vault/v0-vault-stx.clar:331) in a state where it bind a balance before an accrual and assert against it after? Given that it computes cumulative debt from `principal-scaled` and the FORWARD index, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:331` -> `debt-preview`
- Entrypoint: `accrue` (`mainnet/contracts/vault/v0-vault-stx.clar:835`), unprivileged and publicly callable
- Attacker controls: whether an earlier call in the same block already advanced last-update
- Exploit idea: `debt-preview` computes cumulative debt from `principal-scaled` and the FORWARD index. Reach it through `accrue` and bind a balance before an accrual and assert against it after.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `accrue` twice with whether an earlier call in the same block already advanced last-update varied, and assert that the value `debt-preview` returns is identical in both runs; a divergence confirms the finding.
