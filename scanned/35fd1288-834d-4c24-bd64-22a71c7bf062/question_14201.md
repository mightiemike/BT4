Q14201: velocity-envelope bypass in oracle stop-loss state transition when the extension set is active on a production-style pool with non-zero fees

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public `swap` calls through the pool or router with crafted `extensionData` while the extension set is active on a production-style pool with non-zero fees, so that the per-block price-change cap is computed from stale or mis-squared values along `swap -> afterSwap hook -> PoolStateLibrary reads -> metrics -> watermark update or revert`, corrupting current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions? A public trader can choose the exact moment and direction that touches the watermarks, so the guard must fail closed on boundary conditions. Use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting.

Target
- File/function: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol::afterSwap and _afterSwapOracleStopLoss
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public `swap` calls through the pool or router with crafted `extensionData`
- Exploit idea: Reach `swap -> afterSwap hook -> PoolStateLibrary reads -> metrics -> watermark update or revert` in a live public flow and show that use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting. The exact value at risk is current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions.
- Invariant to test: Allowed price movement per block must remain within the exact configured squared-envelope check. The concrete assertion should cover current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions.
- Expected Immunefi impact: High if manipulated or stale prices can still reach live swaps.
- Fast validation: Drive swaps that move the metric exactly to, above, and below the floor and assert the stop-loss always blocks the bad direction before user funds leak.
