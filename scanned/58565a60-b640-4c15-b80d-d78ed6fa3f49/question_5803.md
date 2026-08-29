# Q5803: receive-tokens via liquidate-redeem: absorb a sub-step failure into a fold flag and proceed on 

## Question
`receive-tokens` (mainnet/contracts/market/v0-market-vault.clar:256) pulls an asset from a named account. Can an unprivileged caller of `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604), by choosing the borrower targeted, use that to absorb a sub-step failure into a fold flag and proceed on partial state, violating the invariant that every second of elapsed time is charged exactly once, to one index, in one direction and producing direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:256` -> `receive-tokens`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the borrower targeted
- Exploit idea: `receive-tokens` pulls an asset from a named account. Reach it through `liquidate-redeem` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate-redeem` with the borrower targeted, then read `receive-tokens` state before and after in the same block and assert the two sides of the invariant are equal.
