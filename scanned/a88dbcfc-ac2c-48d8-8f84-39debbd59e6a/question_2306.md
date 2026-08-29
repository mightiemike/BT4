# Q2306: relevant via liquidate: reuse one price and index snapshot across a batch that mut

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling `collateral-receiver`, can an unprivileged attacker make `relevant` (mainnet/contracts/market/v0-market-vault.clar:175) reuse one price and index snapshot across a batch that mutates state between entries? `relevant` drops any position row whose bit is not present in the enabled mask, so the invariant that the state a safety check approved is the state the money movement executes against would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:175` -> `relevant`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `relevant` drops any position row whose bit is not present in the enabled mask. Reach it through `liquidate` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `collateral-receiver` varied, and assert that the value `relevant` returns is identical in both runs; a divergence confirms the finding.
