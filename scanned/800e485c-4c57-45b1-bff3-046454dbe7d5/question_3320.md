# Q3320: resolve-pyth via collateral-remove-redeem: strand value on the market contract when a later step of a

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls `receiver` for the underlying leg reach `resolve-pyth` (mainnet/contracts/market/v0-4-market.clar:312) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it reads the Pyth storage record for a 32-byte ident, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:312` -> `resolve-pyth`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `receiver` for the underlying leg
- Exploit idea: `resolve-pyth` reads the Pyth storage record for a 32-byte ident. Reach it through `collateral-remove-redeem` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `collateral-remove-redeem` twice with `receiver` for the underlying leg varied, and assert that the value `resolve-pyth` returns is identical in both runs; a divergence confirms the finding.
