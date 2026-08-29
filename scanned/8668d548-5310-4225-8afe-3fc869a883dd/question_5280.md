# Q5280: debt-preview via accrue: consume a cache entry after the vault it describes has alr

## Question
Does `accrue` (mainnet/contracts/vault/v0-vault-stx.clar:835) let an unprivileged attacker who controls the utilization the rate is interpolated at reach `debt-preview` (mainnet/contracts/vault/v0-vault-stx.clar:331) in a state where it consume a cache entry after the vault it describes has already moved? Given that it computes cumulative debt from `principal-scaled` and the FORWARD index, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:331` -> `debt-preview`
- Entrypoint: `accrue` (`mainnet/contracts/vault/v0-vault-stx.clar:835`), unprivileged and publicly callable
- Attacker controls: the utilization the rate is interpolated at
- Exploit idea: `debt-preview` computes cumulative debt from `principal-scaled` and the FORWARD index. Reach it through `accrue` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the utilization the rate is interpolated at across its boundary values through `accrue` in simnet and assert `debt-preview` never returns a value that breaks the invariant.
