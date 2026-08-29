# Q5760: get-cached-indexes via accrue: pass a health check and then change, in the same transacti

## Question
Does `accrue` (mainnet/contracts/vault/v0-vault-stx.clar:835) let an unprivileged attacker who controls the utilization the rate is interpolated at reach `get-cached-indexes` (mainnet/contracts/market/v0-4-market.clar:944) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:944` -> `get-cached-indexes`
- Entrypoint: `accrue` (`mainnet/contracts/vault/v0-vault-stx.clar:835`), unprivileged and publicly callable
- Attacker controls: the utilization the rate is interpolated at
- Exploit idea: `get-cached-indexes` reads the per-block cache entry that `resolve-ztoken` and the debt conversions depend on. Reach it through `accrue` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the utilization the rate is interpolated at across its boundary values through `accrue` in simnet and assert `get-cached-indexes` never returns a value that breaks the invariant.
