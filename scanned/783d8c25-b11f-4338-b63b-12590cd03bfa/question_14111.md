Q14111: payload mismatch in oracle stop-loss state transition when the pool is paused for swaps but LP withdrawals remain live

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with permissionless pool creation that wires a legitimate extension set at deployment while the pool is paused for swaps but LP withdrawals remain live, so that extension payload bytes are valid but delivered to the wrong hop or wrong liquidity operation along `swap -> afterSwap hook -> PoolStateLibrary reads -> metrics -> watermark update or revert`, corrupting current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions? A public trader can choose the exact moment and direction that touches the watermarks, so the guard must fail closed on boundary conditions. Use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step.

Target
- File/function: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol::afterSwap and _afterSwapOracleStopLoss
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: permissionless pool creation that wires a legitimate extension set at deployment
- Exploit idea: Reach `swap -> afterSwap hook -> PoolStateLibrary reads -> metrics -> watermark update or revert` in a live public flow and show that use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step. The exact value at risk is current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions.
- Invariant to test: Each extension invocation must consume only the bytes intended for that exact public action. The concrete assertion should cover current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions.
- Expected Immunefi impact: High if a required guard can be bypassed through wrong payload routing.
- Fast validation: Drive swaps that move the metric exactly to, above, and below the floor and assert the stop-loss always blocks the bad direction before user funds leak.
