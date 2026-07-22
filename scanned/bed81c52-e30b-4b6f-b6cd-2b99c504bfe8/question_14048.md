Q14048: wrong-actor binding in oracle stop-loss state transition when the extension set is active on a production-style pool with non-zero fees

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with alternating swap directions across consecutive blocks to stress rolling observations while the extension set is active on a production-style pool with non-zero fees, so that the hook checks the wrong actor among sender, owner, payer, or recipient along `swap -> afterSwap hook -> PoolStateLibrary reads -> metrics -> watermark update or revert`, corrupting current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions? A public trader can choose the exact moment and direction that touches the watermarks, so the guard must fail closed on boundary conditions. Separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate.

Target
- File/function: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol::afterSwap and _afterSwapOracleStopLoss
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: alternating swap directions across consecutive blocks to stress rolling observations
- Exploit idea: Reach `swap -> afterSwap hook -> PoolStateLibrary reads -> metrics -> watermark update or revert` in a live public flow and show that separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate. The exact value at risk is current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions.
- Invariant to test: Every guard must key authorization to the same actor that the economic action is actually attributed to. The concrete assertion should cover current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions.
- Expected Immunefi impact: High direct loss or policy bypass on curated pools.
- Fast validation: Drive swaps that move the metric exactly to, above, and below the floor and assert the stop-loss always blocks the bad direction before user funds leak.
