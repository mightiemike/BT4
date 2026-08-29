# Q5628: next-index via borrow: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the future mask produced by the new debt bit reach `next-index` (mainnet/contracts/vault/v0-vault-stx.clar:379) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it returns the stale `index` unchanged when the accrue pause state is set, instead of reverting, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:379` -> `next-index`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the future mask produced by the new debt bit
- Exploit idea: `next-index` returns the stale `index` unchanged when the accrue pause state is set, instead of reverting. Reach it through `borrow` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the future mask produced by the new debt bit across its boundary values through `borrow` in simnet and assert `next-index` never returns a value that breaks the invariant.
