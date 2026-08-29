# Q1430: calc-liq-debt-repay via liquidate-multi: absorb a sub-step failure into a fold flag and proceed on 

## Question
Entering through `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) while controlling which borrowers are placed early versus late in the batch, can an unprivileged attacker make `calc-liq-debt-repay` (mainnet/contracts/market/v0-4-market.clar:723) absorb a sub-step failure into a fold flag and proceed on partial state? `calc-liq-debt-repay` takes the liquidation factor times the debt with `mul-bps-down`, so the invariant that a failed sub-step aborts the transaction or is explicitly compensated would fail, yielding permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:723` -> `calc-liq-debt-repay`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: which borrowers are placed early versus late in the batch
- Exploit idea: `calc-liq-debt-repay` takes the liquidation factor times the debt with `mul-bps-down`. Reach it through `liquidate-multi` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `liquidate-multi` twice with which borrowers are placed early versus late in the batch varied, and assert that the value `calc-liq-debt-repay` returns is identical in both runs; a divergence confirms the finding.
