Q14420: hook skip or reorder in oracle stop-loss high-watermark math when the guard threshold is hit exactly after a small state-moving precursor action

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with two-transaction public sequences that move the pool just before the guarded call while the guard threshold is hit exactly after a small state-moving precursor action, so that a configured extension hook is reachable but executes in the wrong order or not at all along `public swap -> afterSwap -> per-bin metric computation against decayed watermarks`, corrupting per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage? The attacker cannot change admin settings, but can time public swaps against bins whose metrics sit exactly on the decay boundary. Use a public action that traverses multiple extensions and look for an ordering gap that changes the effective protection boundary.

Target
- File/function: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol::{_metrics,_decayed,_applyWatermark}
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: two-transaction public sequences that move the pool just before the guarded call
- Exploit idea: Reach `public swap -> afterSwap -> per-bin metric computation against decayed watermarks` in a live public flow and show that use a public action that traverses multiple extensions and look for an ordering gap that changes the effective protection boundary. The exact value at risk is per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage.
- Invariant to test: Configured hooks must execute exactly once and in the validated order on every live user flow. The concrete assertion should cover per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage.
- Expected Immunefi impact: High direct loss if a protection hook silently fails open.
- Fast validation: Model decayed watermark progression over time and assert the public swap path never permits a trade that should have triggered the configured floor.
