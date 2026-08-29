# Q3355: oracle-price-legal via borrow: strand value on the market contract when a later step of a

## Question
`oracle-price-legal` (mainnet/contracts/market/v0-4-market.clar:362) accepts any price strictly greater than zero, with no upper bound and no sanity band. Can an unprivileged caller of `borrow` (mainnet/contracts/market/v0-4-market.clar:1238), by choosing the `price-feeds` buffers, use that to strand value on the market contract when a later step of a composite call fails, violating the invariant that a value read from `index-cache` describes the vault as it is at the moment of use and producing direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:362` -> `oracle-price-legal`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers
- Exploit idea: `oracle-price-legal` accepts any price strictly greater than zero, with no upper bound and no sanity band. Reach it through `borrow` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `borrow` with the `price-feeds` buffers, then read `oracle-price-legal` state before and after in the same block and assert the two sides of the invariant are equal.
