# Q4688: calc-liquidation-params via liquidate-redeem: strand value on the market contract when a later step of a

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the borrower targeted reach `calc-liquidation-params` (mainnet/contracts/market/v0-4-market.clar:739) in a state where it strand value on the market contract when a later step of a composite call fails? Given that it chains the factor, the exponent curve, the penalty bound and the max repayable amount in one helper, the invariant that a value read from `index-cache` describes the vault as it is at the moment of use breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:739` -> `calc-liquidation-params`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the borrower targeted
- Exploit idea: `calc-liquidation-params` chains the factor, the exponent curve, the penalty bound and the max repayable amount in one helper. Reach it through `liquidate-redeem` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `liquidate-redeem` twice with the borrower targeted varied, and assert that the value `calc-liquidation-params` returns is identical in both runs; a divergence confirms the finding.
