# Q3570: receive-underlying via deposit: pass a health check and then change, in the same transacti

## Question
Entering through `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763) while controlling `recipient`, including a contract principal, can an unprivileged attacker make `receive-underlying` (mainnet/contracts/vault/v0-vault-stx.clar:291) pass a health check and then change, in the same transaction, the quantity it checked? `receive-underlying` pulls the underlying from a named account, so the invariant that a value read from `index-cache` describes the vault as it is at the moment of use would fail, yielding permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:291` -> `receive-underlying`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: `recipient`, including a contract principal
- Exploit idea: `receive-underlying` pulls the underlying from a named account. Reach it through `deposit` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `recipient`, including a contract principal across its boundary values through `deposit` in simnet and assert `receive-underlying` never returns a value that breaks the invariant.
