# Q2316: get-liquidation-position via liquidate-redeem: strand value on the market contract when a later step of a

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the redemption receiver reach `get-liquidation-position` (mainnet/contracts/market/v0-4-market.clar:473) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it returns enabled collateral plus ALL debt, a different view from the one borrow validated against, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:473` -> `get-liquidation-position`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the redemption receiver
- Exploit idea: `get-liquidation-position` returns enabled collateral plus ALL debt, a different view from the one borrow validated against. Reach it through `liquidate-redeem` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the redemption receiver across its boundary values through `liquidate-redeem` in simnet and assert `get-liquidation-position` never returns a value that breaks the invariant.
