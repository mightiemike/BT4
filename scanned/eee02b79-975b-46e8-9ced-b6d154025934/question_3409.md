# Q3409: receive-underlying via redeem: pass a health check and then change, in the same transacti

## Question
Can an unprivileged attacker entering through `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797), controlling `min-out`, drive `receive-underlying` (mainnet/contracts/vault/v0-vault-stx.clar:291) — which pulls the underlying from a named account — to pass a health check and then change, in the same transaction, the quantity it checked, breaking the invariant that the state a safety check approved is the state the money movement executes against, and cause theft of unclaimed yield?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:291` -> `receive-underlying`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `min-out`
- Exploit idea: `receive-underlying` pulls the underlying from a named account. Reach it through `redeem` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: In `local-testing/tests` on a local fork, drive `redeem` with `min-out`, then read `receive-underlying` state before and after in the same block and assert the two sides of the invariant are equal.
