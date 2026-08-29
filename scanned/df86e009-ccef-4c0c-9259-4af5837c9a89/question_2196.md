# Q2196: calc-index-next via repay: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls `on-behalf-of`, naming any third-party principal reach `calc-index-next` (mainnet/contracts/vault/v0-vault-stx.clar:183) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it applies a multiplier to the current index, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:183` -> `calc-index-next`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: `on-behalf-of`, naming any third-party principal
- Exploit idea: `calc-index-next` applies a multiplier to the current index. Reach it through `repay` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `on-behalf-of`, naming any third-party principal across its boundary values through `repay` in simnet and assert `calc-index-next` never returns a value that breaks the invariant.
