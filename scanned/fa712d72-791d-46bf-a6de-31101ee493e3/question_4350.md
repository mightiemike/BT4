# Q4350: calc-liq-debt-repay-real via liquidate: pass a health check and then change, in the same transacti

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling `collateral-receiver`, can an unprivileged attacker make `calc-liq-debt-repay-real` (mainnet/contracts/market/v0-4-market.clar:733) pass a health check and then change, in the same transaction, the quantity it checked? `calc-liq-debt-repay-real` re-derives debt from capped collateral by dividing by `(+ BPS liq-penalty)`, so the invariant that every second of elapsed time is charged exactly once, to one index, in one direction would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:733` -> `calc-liq-debt-repay-real`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `calc-liq-debt-repay-real` re-derives debt from capped collateral by dividing by `(+ BPS liq-penalty)`. Reach it through `liquidate` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `collateral-receiver` across its boundary values through `liquidate` in simnet and assert `calc-liq-debt-repay-real` never returns a value that breaks the invariant.
