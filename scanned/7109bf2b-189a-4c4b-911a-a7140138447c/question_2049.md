# Q2049: active via liquidate: absorb a sub-step failure into a fold flag and proceed on 

## Question
Can an unprivileged attacker entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382), controlling which collateral and debt asset pair is targeted, drive `active` (mainnet/contracts/registry/v0-egroup.clar:238) — which lists candidate bucket masks at or above a population — to absorb a sub-step failure into a fold flag and proceed on partial state, breaking the invariant that every second of elapsed time is charged exactly once, to one index, in one direction, and cause permanent freezing of a position that can never be closed?

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:238` -> `active`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: which collateral and debt asset pair is targeted
- Exploit idea: `active` lists candidate bucket masks at or above a population. Reach it through `liquidate` and absorb a sub-step failure into a fold flag and proceed on partial state.
- Invariant to test: every second of elapsed time is charged exactly once, to one index, in one direction
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Snapshot every state variable `active` touches, run `liquidate` with which collateral and debt asset pair is targeted, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
