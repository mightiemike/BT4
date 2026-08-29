# Q3416: accrue-collateral-asset via liquidate-redeem: pass a health check and then change, in the same transacti

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the vault whose share price the redemption moves reach `accrue-collateral-asset` (mainnet/contracts/market/v0-4-market.clar:273) in a state where it pass a health check and then change, in the same transaction, the quantity it checked? Given that it maps a ztoken id to a vault id through a chain of `is-eq` tests that falls through to the u100 sentinel, the invariant that a failed sub-step aborts the transaction or is explicitly compensated breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:273` -> `accrue-collateral-asset`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the vault whose share price the redemption moves
- Exploit idea: `accrue-collateral-asset` maps a ztoken id to a vault id through a chain of `is-eq` tests that falls through to the u100 sentinel. Reach it through `liquidate-redeem` and pass a health check and then change, in the same transaction, the quantity it checked.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `liquidate-redeem` twice with the vault whose share price the redemption moves varied, and assert that the value `accrue-collateral-asset` returns is identical in both runs; a divergence confirms the finding.
