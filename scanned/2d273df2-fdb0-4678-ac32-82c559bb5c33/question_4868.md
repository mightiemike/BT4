# Q4868: calc-multiplier-delta via deposit: pass a health check and then change, in the same transacti

## Question
Does `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763) let an unprivileged attacker who controls `min-out` reach `calc-multiplier-delta` (mainnet/contracts/vault/v0-vault-stx.clar:170) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it compounds a rate over `time-delta` with a caller-independent rounding flag, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:170` -> `calc-multiplier-delta`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: `min-out`
- Exploit idea: `calc-multiplier-delta` compounds a rate over `time-delta` with a caller-independent rounding flag. Reach it through `deposit` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `deposit` twice with `min-out` varied, and assert that the value `calc-multiplier-delta` returns is identical in both runs; a divergence confirms the finding.
