Q14772: timed-threshold manipulation in oracle stop-loss high-watermark math when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with two-transaction public sequences that move the pool just before the guarded call while the guarded operation touches only the active bin and one adjacent bin, so that a small public precursor action can place the next guarded trade just on the permissive side of a threshold that should have blocked it along `public swap -> afterSwap -> per-bin metric computation against decayed watermarks`, corrupting per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage? The attacker cannot change admin settings, but can time public swaps against bins whose metrics sit exactly on the decay boundary. Use two public transactions in sequence so the second one benefits from stale threshold state or observation updates.

Target
- File/function: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol::{_metrics,_decayed,_applyWatermark}
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: two-transaction public sequences that move the pool just before the guarded call
- Exploit idea: Reach `public swap -> afterSwap -> per-bin metric computation against decayed watermarks` in a live public flow and show that use two public transactions in sequence so the second one benefits from stale threshold state or observation updates. The exact value at risk is per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage.
- Invariant to test: Guard thresholds must update atomically enough that one public action cannot invalidate the safety assumptions of the next. The concrete assertion should cover per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage.
- Expected Immunefi impact: High if timing lets an attacker bypass a live loss-prevention control.
- Fast validation: Model decayed watermark progression over time and assert the public swap path never permits a trade that should have triggered the configured floor.
