# Q1339: resolve-interpolation-points via collateral-remove: absorb a sub-step failure into a fold flag and proceed on 

## Question
`resolve-interpolation-points` (mainnet/contracts/vault/v0-vault-stx.clar:205) selects the bracketing curve points for a utilization. Can an unprivileged caller of `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107), by choosing `receiver`, including a contract principal, use that to absorb a sub-step failure into a fold flag and proceed on partial state, violating the invariant that a value read from `index-cache` describes the vault as it is at the moment of use and producing theft of unclaimed yield?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:205` -> `resolve-interpolation-points`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: `receiver`, including a contract principal
- Exploit idea: `resolve-interpolation-points` selects the bracketing curve points for a utilization. Reach it through `collateral-remove` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: In `local-testing/tests` on a local fork, drive `collateral-remove` with `receiver`, including a contract principal, then read `resolve-interpolation-points` state before and after in the same block and assert the two sides of the invariant are equal.
