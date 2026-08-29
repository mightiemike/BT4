# Q4572: write-feed via liquidate: bind a balance before an accrual and assert against it aft

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `collateral-receiver` reach `write-feed` (mainnet/contracts/market/v0-4-market.clar:129) in a state where it bind a balance before an accrual and assert against it after? Given that it applies one Pyth price-feed update and folds its status, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:129` -> `write-feed`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `write-feed` applies one Pyth price-feed update and folds its status. Reach it through `liquidate` and bind a balance before an accrual and assert against it after.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `collateral-receiver` across its boundary values through `liquidate` in simnet and assert `write-feed` never returns a value that breaks the invariant.
