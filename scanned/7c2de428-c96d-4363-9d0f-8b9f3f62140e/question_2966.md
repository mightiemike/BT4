# Q2966: create via liquidate: bind a balance before an accrual and assert against it aft

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling which collateral and debt asset pair is targeted, can an unprivileged attacker make `create` (mainnet/contracts/market/v0-market-vault.clar:150) bind a balance before an accrual and assert against it after? `create` binds a principal to a fresh numeric id, so the invariant that a failed sub-step aborts the transaction or is explicitly compensated would fail, yielding protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:150` -> `create`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: which collateral and debt asset pair is targeted
- Exploit idea: `create` binds a principal to a fresh numeric id. Reach it through `liquidate` and bind a balance before an accrual and assert against it after.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with which collateral and debt asset pair is targeted varied, and assert that the value `create` returns is identical in both runs; a divergence confirms the finding.
