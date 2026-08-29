# Q3791: calc-liq-collateral-repay via liquidate-multi: leave the accrual clock stale so a later interval double-c

## Question
`calc-liq-collateral-repay` (mainnet/contracts/market/v0-4-market.clar:728) scales the repaid debt by `(+ BPS liq-penalty)`. Can an unprivileged caller of `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593), by choosing how many entries share one price snapshot (price-feeds is passed as none), use that to leave the accrual clock stale so a later interval double-counts elapsed time, violating the invariant that a failed sub-step aborts the transaction or is explicitly compensated and producing protocol insolvency?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:728` -> `calc-liq-collateral-repay`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: how many entries share one price snapshot (price-feeds is passed as none)
- Exploit idea: `calc-liq-collateral-repay` scales the repaid debt by `(+ BPS liq-penalty)`. Reach it through `liquidate-multi` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Run the baseline `liquidate-multi` call, then the attacker-shaped one with how many entries share one price snapshot (price-feeds is passed as none), and assert the attacker's net token balance change is zero or negative.
