# Q4800: get-full-position via liquidate: strand value on the market contract when a later step of a

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `collateral-receiver` reach `get-full-position` (mainnet/contracts/market/v0-4-market.clar:470) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it returns all collateral rows regardless of the enabled bitmap, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:470` -> `get-full-position`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `get-full-position` returns all collateral rows regardless of the enabled bitmap. Reach it through `liquidate` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `collateral-receiver` across its boundary values through `liquidate` in simnet and assert `get-full-position` never returns a value that breaks the invariant.
