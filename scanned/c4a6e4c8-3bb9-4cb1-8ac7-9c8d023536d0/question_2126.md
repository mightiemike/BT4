# Q2126: relevant via borrow: absorb a sub-step failure into a fold flag and proceed on 

## Question
Entering through `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) while controlling the `price-feeds` buffers, can an unprivileged attacker make `relevant` (mainnet/contracts/market/v0-market-vault.clar:175) absorb a sub-step failure into a fold flag and proceed on partial state? `relevant` drops any position row whose bit is not present in the enabled mask, so the invariant that the state a safety check approved is the state the money movement executes against would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:175` -> `relevant`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers
- Exploit idea: `relevant` drops any position row whose bit is not present in the enabled mask. Reach it through `borrow` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with the `price-feeds` buffers varied, and assert that the value `relevant` returns is identical in both runs; a divergence confirms the finding.
