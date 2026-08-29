# Q3612: send-underlying via transfer: pass a health check and then change, in the same transacti

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls the timing relative to a pledge or a liquidation reach `send-underlying` (mainnet/contracts/vault/v0-vault-stx.clar:296) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it pushes the underlying under an `as-contract?` post-condition scope, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:296` -> `send-underlying`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: the timing relative to a pledge or a liquidation
- Exploit idea: `send-underlying` pushes the underlying under an `as-contract?` post-condition scope. Reach it through `transfer` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the timing relative to a pledge or a liquidation across its boundary values through `transfer` in simnet and assert `send-underlying` never returns a value that breaks the invariant.
