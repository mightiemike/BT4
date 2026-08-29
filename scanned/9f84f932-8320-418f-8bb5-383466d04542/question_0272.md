# Q0272: increment via collateral-add: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls `amount` reach `increment` (mainnet/contracts/market/v0-market-vault.clar:137) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it advances the user-id nonce, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:137` -> `increment`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `increment` advances the user-id nonce. Reach it through `collateral-add` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with `amount` varied, and assert that the value `increment` returns is identical in both runs; a divergence confirms the finding.
