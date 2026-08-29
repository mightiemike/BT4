# Q3488: get-available-assets via transfer: strand value on the market contract when a later step of a

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls `amount` reach `get-available-assets` (mainnet/contracts/vault/v0-vault-stx.clar:481) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it reads real liquidity, a different quantity from the `assets` var that `redeem` also gates on, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:481` -> `get-available-assets`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `get-available-assets` reads real liquidity, a different quantity from the `assets` var that `redeem` also gates on. Reach it through `transfer` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `transfer` twice with `amount` varied, and assert that the value `get-available-assets` returns is identical in both runs; a divergence confirms the finding.
