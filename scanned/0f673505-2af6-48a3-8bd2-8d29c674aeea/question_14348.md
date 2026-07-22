Q14348: paused-flow regression in oracle stop-loss state transition when the pool is paused for swaps but LP withdrawals remain live

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with two-transaction public sequences that move the pool just before the guarded call while the pool is paused for swaps but LP withdrawals remain live, so that a paused pool still exposes a public flow that an extension handles using active-swap assumptions along `swap -> afterSwap hook -> PoolStateLibrary reads -> metrics -> watermark update or revert`, corrupting current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions? A public trader can choose the exact moment and direction that touches the watermarks, so the guard must fail closed on boundary conditions. Withdraw or otherwise interact during pause and look for an extension branch that assumes swaps are still active.

Target
- File/function: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol::afterSwap and _afterSwapOracleStopLoss
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: two-transaction public sequences that move the pool just before the guarded call
- Exploit idea: Reach `swap -> afterSwap hook -> PoolStateLibrary reads -> metrics -> watermark update or revert` in a live public flow and show that withdraw or otherwise interact during pause and look for an extension branch that assumes swaps are still active. The exact value at risk is current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions.
- Invariant to test: Pause semantics must remain coherent across core logic and every active extension callback. The concrete assertion should cover current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions.
- Expected Immunefi impact: Medium broken core functionality or constrained loss of LP funds.
- Fast validation: Drive swaps that move the metric exactly to, above, and below the floor and assert the stop-loss always blocks the bad direction before user funds leak.
