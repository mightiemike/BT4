# Q0135: accrue-user-debts via liquidate-redeem: leave the accrual clock stale so a later interval double-c

## Question
`accrue-user-debts` (mainnet/contracts/market/v0-4-market.clar:259) folds accrual over the position's debt list only. Can an unprivileged caller of `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604), by choosing the vault whose share price the redemption moves, use that to leave the accrual clock stale so a later interval double-counts elapsed time, violating the invariant that the state a safety check approved is the state the money movement executes against and producing permanent freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:259` -> `accrue-user-debts`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the vault whose share price the redemption moves
- Exploit idea: `accrue-user-debts` folds accrual over the position's debt list only. Reach it through `liquidate-redeem` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Snapshot every state variable `accrue-user-debts` touches, run `liquidate-redeem` with the vault whose share price the redemption moves, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
