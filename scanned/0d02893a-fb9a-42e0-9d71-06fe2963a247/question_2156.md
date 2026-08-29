# Q2156: interpolate-rate via redeem: consume a cache entry after the vault it describes has alr

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls `recipient` reach `interpolate-rate` (mainnet/contracts/vault/v0-vault-stx.clar:196) in a state where it consume a cache entry after the vault it describes has already moved? Given that it interpolates between packed u16 curve points, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:196` -> `interpolate-rate`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `recipient`
- Exploit idea: `interpolate-rate` interpolates between packed u16 curve points. Reach it through `redeem` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `redeem` twice with `recipient` varied, and assert that the value `interpolate-rate` returns is identical in both runs; a divergence confirms the finding.
