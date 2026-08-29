# Q2732: create via collateral-add: bind a balance before an accrual and assert against it aft

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls the position's existing collateral and debt composition reach `create` (mainnet/contracts/market/v0-market-vault.clar:150) in a state where it bind a balance before an accrual and assert against it after? Given that it binds a principal to a fresh numeric id, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:150` -> `create`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: the position's existing collateral and debt composition
- Exploit idea: `create` binds a principal to a fresh numeric id. Reach it through `collateral-add` and bind a balance before an accrual and assert against it after.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with the position's existing collateral and debt composition varied, and assert that the value `create` returns is identical in both runs; a divergence confirms the finding.
