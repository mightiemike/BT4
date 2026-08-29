# Q4392: get-liquidation-position via liquidate: pass a health check and then change, in the same transacti

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `collateral-receiver` reach `get-liquidation-position` (mainnet/contracts/market/v0-4-market.clar:473) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it returns enabled collateral plus ALL debt, a different view from the one borrow validated against, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:473` -> `get-liquidation-position`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `get-liquidation-position` returns enabled collateral plus ALL debt, a different view from the one borrow validated against. Reach it through `liquidate` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz `collateral-receiver` across its boundary values through `liquidate` in simnet and assert `get-liquidation-position` never returns a value that breaks the invariant.
