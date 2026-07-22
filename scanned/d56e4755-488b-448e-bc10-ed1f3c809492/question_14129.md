Q14129: allowlist bypass in oracle stop-loss state transition when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public `swap` calls through the pool or router with crafted `extensionData` while the guarded operation touches only the active bin and one adjacent bin, so that a curated pool's allowlist can be bypassed through a public router or liquidity-adder path along `swap -> afterSwap hook -> PoolStateLibrary reads -> metrics -> watermark update or revert`, corrupting current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions? A public trader can choose the exact moment and direction that touches the watermarks, so the guard must fail closed on boundary conditions. Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.

Target
- File/function: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol::afterSwap and _afterSwapOracleStopLoss
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public `swap` calls through the pool or router with crafted `extensionData`
- Exploit idea: Reach `swap -> afterSwap hook -> PoolStateLibrary reads -> metrics -> watermark update or revert` in a live public flow and show that enter through the supported periphery path rather than the direct pool call and see whether the identity check changes. The exact value at risk is current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions.
- Invariant to test: A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it. The concrete assertion should cover current bin metrics, decayed watermarks, drawdown floor, and the decision to block one or both swap directions.
- Expected Immunefi impact: High direct loss or curation failure if disallowed users can still trade or deposit.
- Fast validation: Drive swaps that move the metric exactly to, above, and below the floor and assert the stop-loss always blocks the bad direction before user funds leak.
