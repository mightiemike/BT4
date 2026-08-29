# Q2760: subset via collateral-remove-redeem: bind a balance before an accrual and assert against it aft

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls `min-underlying` reach `subset` (mainnet/contracts/market/v0-market-vault.clar:100) in a state where it bind a balance before an accrual and assert against it after? Given that it tests bitmask containment, the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:100` -> `subset`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `min-underlying`
- Exploit idea: `subset` tests bitmask containment. Reach it through `collateral-remove-redeem` and bind a balance before an accrual and assert against it after.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `min-underlying` across its boundary values through `collateral-remove-redeem` in simnet and assert `subset` never returns a value that breaks the invariant.
