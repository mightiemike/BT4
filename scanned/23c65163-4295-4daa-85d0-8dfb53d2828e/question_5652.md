# Q5652: principal-ratio-reduction via transfer: leave the accrual clock stale so a later interval double-c

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls `amount` reach `principal-ratio-reduction` (mainnet/contracts/vault/v0-vault-stx.clar:406) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it derives a principal reduction from an amount, the scaled principal and the previewed debt, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:406` -> `principal-ratio-reduction`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `principal-ratio-reduction` derives a principal reduction from an amount, the scaled principal and the previewed debt. Reach it through `transfer` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `amount` across its boundary values through `transfer` in simnet and assert `principal-ratio-reduction` never returns a value that breaks the invariant.
