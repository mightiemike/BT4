# Q0746: oracle-price-legal via liquidate: reuse one price and index snapshot across a batch that mut

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling `debt-amount`, can an unprivileged attacker make `oracle-price-legal` (mainnet/contracts/market/v0-4-market.clar:362) reuse one price and index snapshot across a batch that mutates state between entries? `oracle-price-legal` accepts any price strictly greater than zero, with no upper bound and no sanity band, so the invariant that a value read from `index-cache` describes the vault as it is at the moment of use would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:362` -> `oracle-price-legal`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `debt-amount`
- Exploit idea: `oracle-price-legal` accepts any price strictly greater than zero, with no upper bound and no sanity band. Reach it through `liquidate` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `debt-amount` varied, and assert that the value `oracle-price-legal` returns is identical in both runs; a divergence confirms the finding.
