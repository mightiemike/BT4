# Q2792: linear-interpolate via collateral-add: strand value on the market contract when a later step of a

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls the position's existing collateral and debt composition reach `linear-interpolate` (mainnet/contracts/vault/v0-vault-stx.clar:221) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it interpolates between two points, dividing by `(- x2 x1)`, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:221` -> `linear-interpolate`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the position's existing collateral and debt composition
- Exploit idea: `linear-interpolate` interpolates between two points, dividing by `(- x2 x1)`. Reach it through `collateral-add` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with the position's existing collateral and debt composition varied, and assert that the value `linear-interpolate` returns is identical in both runs; a divergence confirms the finding.
