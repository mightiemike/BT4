# Q2846: zip via redeem: pass a health check and then change, in the same transacti

## Question
Entering through `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) while controlling `recipient`, can an unprivileged attacker make `zip` (mainnet/contracts/vault/v0-vault-stx.clar:226) pass a health check and then change, in the same transaction, the quantity it checked? `zip` pairs the utilization and rate point lists element by element, so the invariant that the state a safety check approved is the state the money movement executes against would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:226` -> `zip`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `recipient`
- Exploit idea: `zip` pairs the utilization and rate point lists element by element. Reach it through `redeem` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `redeem` twice with `recipient` varied, and assert that the value `zip` returns is identical in both runs; a divergence confirms the finding.
