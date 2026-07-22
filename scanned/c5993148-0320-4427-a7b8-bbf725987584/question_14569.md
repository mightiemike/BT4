Q14569: watermark boundary bug in oracle stop-loss high-watermark math when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public `swap` calls through the pool or router with crafted `extensionData` while the guarded operation touches only the active bin and one adjacent bin, so that the stop-loss threshold is hit but the public swap still executes because the boundary math fails open along `public swap -> afterSwap -> per-bin metric computation against decayed watermarks`, corrupting per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage? The attacker cannot change admin settings, but can time public swaps against bins whose metrics sit exactly on the decay boundary. Move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction.

Target
- File/function: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol::{_metrics,_decayed,_applyWatermark}
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public `swap` calls through the pool or router with crafted `extensionData`
- Exploit idea: Reach `public swap -> afterSwap -> per-bin metric computation against decayed watermarks` in a live public flow and show that move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction. The exact value at risk is per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage.
- Invariant to test: Stop-loss protection must fail closed at every watermark boundary that should block further value leakage. The concrete assertion should cover per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage.
- Expected Immunefi impact: High bad-price or value-leak execution that the configured protection was supposed to stop.
- Fast validation: Model decayed watermark progression over time and assert the public swap path never permits a trade that should have triggered the configured floor.
