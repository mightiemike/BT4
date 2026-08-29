# Q5108: oracle-price-legal via liquidate-multi: reuse one price and index snapshot across a batch that mut

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls the full batch list and its ordering reach `oracle-price-legal` (mainnet/contracts/market/v0-4-market.clar:362) in a state where it reuse one price and index snapshot across a batch that mutates state between entries? Given that it accepts any price strictly greater than zero, with no upper bound and no sanity band, the invariant that every second of elapsed time is charged exactly once, to one index, in one direction breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:362` -> `oracle-price-legal`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the full batch list and its ordering
- Exploit idea: `oracle-price-legal` accepts any price strictly greater than zero, with no upper bound and no sanity band. Reach it through `liquidate-multi` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `liquidate-multi` twice with the full batch list and its ordering varied, and assert that the value `oracle-price-legal` returns is identical in both runs; a divergence confirms the finding.
