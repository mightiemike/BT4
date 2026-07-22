Q14460: wrong-actor binding in oracle stop-loss high-watermark math when the guard threshold is hit exactly after a small state-moving precursor action

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with two-transaction public sequences that move the pool just before the guarded call while the guard threshold is hit exactly after a small state-moving precursor action, so that the hook checks the wrong actor among sender, owner, payer, or recipient along `public swap -> afterSwap -> per-bin metric computation against decayed watermarks`, corrupting per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage? The attacker cannot change admin settings, but can time public swaps against bins whose metrics sit exactly on the decay boundary. Separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate.

Target
- File/function: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol::{_metrics,_decayed,_applyWatermark}
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: two-transaction public sequences that move the pool just before the guarded call
- Exploit idea: Reach `public swap -> afterSwap -> per-bin metric computation against decayed watermarks` in a live public flow and show that separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate. The exact value at risk is per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage.
- Invariant to test: Every guard must key authorization to the same actor that the economic action is actually attributed to. The concrete assertion should cover per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage.
- Expected Immunefi impact: High direct loss or policy bypass on curated pools.
- Fast validation: Model decayed watermark progression over time and assert the public swap path never permits a trade that should have triggered the configured floor.
