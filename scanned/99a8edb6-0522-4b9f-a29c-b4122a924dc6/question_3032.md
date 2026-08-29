# Q3032: debt-remove-scaled via repay: absorb a sub-step failure into a fold flag and proceed on 

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls the `ft` trait principal reach `debt-remove-scaled` (mainnet/contracts/market/v0-market-vault.clar:473) in a state where it absorb a sub-step failure into a fold flag and proceed on partial state? Given that it clears the debt bit only when the remaining scaled debt is exactly zero, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:473` -> `debt-remove-scaled`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: the `ft` trait principal
- Exploit idea: `debt-remove-scaled` clears the debt bit only when the remaining scaled debt is exactly zero. Reach it through `repay` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `repay` twice with the `ft` trait principal varied, and assert that the value `debt-remove-scaled` returns is identical in both runs; a divergence confirms the finding.
