# Q2312: add-user-collateral via supply-collateral-add: bind a balance before an accrual and assert against it aft

## Question
Does `supply-collateral-add` (mainnet/contracts/market/v0-4-market.clar:1175) let an unprivileged attacker who controls vault share price at the moment of the deposit leg reach `add-user-collateral` (mainnet/contracts/market/v0-market-vault.clar:198) in a state where it bind a balance before an accrual and assert against it after? Given that it adds to the collateral row with a graceful u0 default, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:198` -> `add-user-collateral`
- Entrypoint: `supply-collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1175`), unprivileged and publicly callable
- Attacker controls: vault share price at the moment of the deposit leg
- Exploit idea: `add-user-collateral` adds to the collateral row with a graceful u0 default. Reach it through `supply-collateral-add` and bind a balance before an accrual and assert against it after.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `supply-collateral-add` twice with vault share price at the moment of the deposit leg varied, and assert that the value `add-user-collateral` returns is identical in both runs; a divergence confirms the finding.
