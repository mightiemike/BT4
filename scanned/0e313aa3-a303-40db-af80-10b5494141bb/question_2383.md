# Q2383: calc-liq-factor-bound via liquidate-redeem: reuse one price and index snapshot across a batch that mut

## Question
`calc-liq-factor-bound` (mainnet/contracts/market/v0-4-market.clar:718) scales the penalty between a min and a max, capped at the max. Can an unprivileged caller of `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604), by choosing the seized zToken amount that is immediately redeemed, use that to reuse one price and index snapshot across a batch that mutates state between entries, violating the invariant that a failed sub-step aborts the transaction or is explicitly compensated and producing direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:718` -> `calc-liq-factor-bound`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the seized zToken amount that is immediately redeemed
- Exploit idea: `calc-liq-factor-bound` scales the penalty between a min and a max, capped at the max. Reach it through `liquidate-redeem` and reuse one price and index snapshot across a batch that mutates state between entries.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate-redeem` with the seized zToken amount that is immediately redeemed, then read `calc-liq-factor-bound` state before and after in the same block and assert the two sides of the invariant are equal.
