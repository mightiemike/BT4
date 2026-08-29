# Q4388: receive-underlying via redeem: consume a cache entry after the vault it describes has alr

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls `min-out` reach `receive-underlying` (mainnet/contracts/vault/v0-vault-stx.clar:291) in a state where it consume a cache entry after the vault it describes has already moved? Given that it pulls the underlying from a named account, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:291` -> `receive-underlying`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `min-out`
- Exploit idea: `receive-underlying` pulls the underlying from a named account. Reach it through `redeem` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `redeem` twice with `min-out` varied, and assert that the value `receive-underlying` returns is identical in both runs; a divergence confirms the finding.
