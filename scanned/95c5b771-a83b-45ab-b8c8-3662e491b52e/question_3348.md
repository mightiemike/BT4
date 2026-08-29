# Q3348: calc-index-next via deposit: strand value on the market contract when a later step of a

## Question
Does `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763) let an unprivileged attacker who controls `amount` reach `calc-index-next` (mainnet/contracts/vault/v0-vault-stx.clar:183) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it applies a multiplier to the current index, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:183` -> `calc-index-next`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `calc-index-next` applies a multiplier to the current index. Reach it through `deposit` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `amount` across its boundary values through `deposit` in simnet and assert `calc-index-next` never returns a value that breaks the invariant.
