Q14542: allowlist bypass in oracle stop-loss high-watermark math when the guard threshold is hit exactly after a small state-moving precursor action

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with bin-local liquidity positions that sit exactly at a guard threshold while the guard threshold is hit exactly after a small state-moving precursor action, so that a curated pool's allowlist can be bypassed through a public router or liquidity-adder path along `public swap -> afterSwap -> per-bin metric computation against decayed watermarks`, corrupting per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage? The attacker cannot change admin settings, but can time public swaps against bins whose metrics sit exactly on the decay boundary. Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.

Target
- File/function: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol::{_metrics,_decayed,_applyWatermark}
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: bin-local liquidity positions that sit exactly at a guard threshold
- Exploit idea: Reach `public swap -> afterSwap -> per-bin metric computation against decayed watermarks` in a live public flow and show that enter through the supported periphery path rather than the direct pool call and see whether the identity check changes. The exact value at risk is per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage.
- Invariant to test: A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it. The concrete assertion should cover per-share token0/token1 metrics, decay clocks, and the one-directional loss floor that should stop further value leakage.
- Expected Immunefi impact: High direct loss or curation failure if disallowed users can still trade or deposit.
- Fast validation: Model decayed watermark progression over time and assert the public swap path never permits a trade that should have triggered the configured floor.
