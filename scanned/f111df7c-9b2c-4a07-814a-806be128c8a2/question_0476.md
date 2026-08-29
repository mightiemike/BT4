# Q0476: get-liquidation-position via liquidate: strand value on the market contract when a later step of a

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `debt-amount` reach `get-liquidation-position` (mainnet/contracts/market/v0-4-market.clar:473) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it returns enabled collateral plus ALL debt, a different view from the one borrow validated against, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:473` -> `get-liquidation-position`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `debt-amount`
- Exploit idea: `get-liquidation-position` returns enabled collateral plus ALL debt, a different view from the one borrow validated against. Reach it through `liquidate` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `debt-amount` varied, and assert that the value `get-liquidation-position` returns is identical in both runs; a divergence confirms the finding.
