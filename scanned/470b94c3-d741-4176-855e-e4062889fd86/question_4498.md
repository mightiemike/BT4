# Q4498: merge-price via liquidate-redeem: absorb a sub-step failure into a fold flag and proceed on 

## Question
Entering through `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) while controlling the redemption receiver, can an unprivileged attacker make `merge-price` (mainnet/contracts/market/v0-4-market.clar:506) absorb a sub-step failure into a fold flag and proceed on partial state? `merge-price` attaches a price to an asset record by position in the fold, not by asset id, so the invariant that a value read from `index-cache` describes the vault as it is at the moment of use would fail, yielding theft of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:506` -> `merge-price`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the redemption receiver
- Exploit idea: `merge-price` attaches a price to an asset record by position in the fold, not by asset id. Reach it through `liquidate-redeem` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: a value read from `index-cache` describes the vault as it is at the moment of use
- Expected Immunefi impact: High - theft of unclaimed yield
- Fast validation: Set up the position in simnet, call `liquidate-redeem` with the redemption receiver, and assert on the printed event plus the post-state that collateral, debt and share totals still reconcile.
