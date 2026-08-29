# Q3648: interpolate-rate via deposit: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763) let an unprivileged attacker who controls `amount` reach `interpolate-rate` (mainnet/contracts/vault/v0-vault-stx.clar:196) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it interpolates between packed u16 curve points, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:196` -> `interpolate-rate`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `interpolate-rate` interpolates between packed u16 curve points. Reach it through `deposit` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `amount` across its boundary values through `deposit` in simnet and assert `interpolate-rate` never returns a value that breaks the invariant.
