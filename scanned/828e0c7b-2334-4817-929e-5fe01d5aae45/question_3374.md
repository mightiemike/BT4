# Q3374: vault-accrue via redeem: pass a health check and then change, in the same transacti

## Question
Entering through `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) while controlling `min-out`, can an unprivileged attacker make `vault-accrue` (mainnet/contracts/market/v0-4-market.clar:189) pass a health check and then change, in the same transaction, the quantity it checked? `vault-accrue` dispatches accrual to one of six vaults by asset id, so the invariant that the state a safety check approved is the state the money movement executes against would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:189` -> `vault-accrue`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `min-out`
- Exploit idea: `vault-accrue` dispatches accrual to one of six vaults by asset id. Reach it through `redeem` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `redeem` twice with `min-out` varied, and assert that the value `vault-accrue` returns is identical in both runs; a divergence confirms the finding.
