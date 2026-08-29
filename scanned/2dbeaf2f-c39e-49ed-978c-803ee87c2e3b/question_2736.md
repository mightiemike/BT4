# Q2736: receive-underlying via transfer: strand value on the market contract when a later step of a

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls `amount` reach `receive-underlying` (mainnet/contracts/vault/v0-vault-stx.clar:291) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it pulls the underlying from a named account, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:291` -> `receive-underlying`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `receive-underlying` pulls the underlying from a named account. Reach it through `transfer` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `amount` across its boundary values through `transfer` in simnet and assert `receive-underlying` never returns a value that breaks the invariant.
