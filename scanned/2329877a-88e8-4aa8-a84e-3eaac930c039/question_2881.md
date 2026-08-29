# Q2881: calc-cumulative-debt via deposit: pass a health check and then change, in the same transacti

## Question
Can an unprivileged attacker entering through `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763), controlling `recipient`, including a contract principal, drive `calc-cumulative-debt` (mainnet/contracts/vault/v0-vault-stx.clar:180) — which multiplies scaled principal by an index — to pass a health check and then change, in the same transaction, the quantity it checked, breaking the invariant that the state a safety check approved is the state the money movement executes against, and cause direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:180` -> `calc-cumulative-debt`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: `recipient`, including a contract principal
- Exploit idea: `calc-cumulative-debt` multiplies scaled principal by an index. Reach it through `deposit` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `deposit` with `recipient`, including a contract principal, then read `calc-cumulative-debt` state before and after in the same block and assert the two sides of the invariant are equal.
