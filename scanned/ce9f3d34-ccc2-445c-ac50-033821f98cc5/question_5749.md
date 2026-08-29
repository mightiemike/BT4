# Q5749: calc-liq-collateral-repay via liquidate-multi: consume a cache entry after the vault it describes has alr

## Question
Can an unprivileged attacker entering through `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593), controlling how many entries share one price snapshot (price-feeds is passed as none), drive `calc-liq-collateral-repay` (mainnet/contracts/market/v0-4-market.clar:728) — which scales the repaid debt by `(+ BPS liq-penalty)` — to consume a cache entry after the vault it describes has already moved, breaking the invariant that when a multi-step entry point aborts, no value is stranded and no identifier survives, and cause theft of unclaimed yield?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:728` -> `calc-liq-collateral-repay`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: how many entries share one price snapshot (price-feeds is passed as none)
- Exploit idea: `calc-liq-collateral-repay` scales the repaid debt by `(+ BPS liq-penalty)`. Reach it through `liquidate-multi` and consume a cache entry after the vault it describes has already moved.
- Invariant to test: when a multi-step entry point aborts, no value is stranded and no identifier survives
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate-multi` with how many entries share one price snapshot (price-feeds is passed as none), then read `calc-liq-collateral-repay` state before and after in the same block and assert the two sides of the invariant are equal.
