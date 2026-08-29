# Q5774: get-position via liquidate-multi: pass a health check and then change, in the same transacti

## Question
Entering through `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) while controlling which borrowers are placed early versus late in the batch, can an unprivileged attacker make `get-position` (mainnet/contracts/market/v0-4-market.clar:466) pass a health check and then change, in the same transaction, the quantity it checked? `get-position` returns only rows whose bit is set in the ENABLED bitmap, so the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives would fail, yielding protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:466` -> `get-position`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: which borrowers are placed early versus late in the batch
- Exploit idea: `get-position` returns only rows whose bit is set in the ENABLED bitmap. Reach it through `liquidate-multi` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `liquidate-multi` twice with which borrowers are placed early versus late in the batch varied, and assert that the value `get-position` returns is identical in both runs; a divergence confirms the finding.
