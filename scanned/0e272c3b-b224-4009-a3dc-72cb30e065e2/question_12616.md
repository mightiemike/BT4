Q12616: velocity-envelope bypass in after-swap hook ordering when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with alternating swap directions across consecutive blocks to stress rolling observations while the guarded operation touches only the active bin and one adjacent bin, so that the per-block price-change cap is computed from stale or mis-squared values along `swap -> settle deltas -> ExtensionCalling._afterSwap -> post-state guard checks`, corrupting packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state? Post-trade protections like stop-loss rely on this hook seeing the same final state the pool already committed. Use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_afterSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: alternating swap directions across consecutive blocks to stress rolling observations
- Exploit idea: Reach `swap -> settle deltas -> ExtensionCalling._afterSwap -> post-state guard checks` in a live public flow and show that use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting. The exact value at risk is packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state.
- Invariant to test: Allowed price movement per block must remain within the exact configured squared-envelope check. The concrete assertion should cover packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state.
- Expected Immunefi impact: High if manipulated or stale prices can still reach live swaps.
- Fast validation: Assert every after-swap hook sees the exact final slot0 and delta values committed by the pool and cannot be skipped or misordered for public swaps.
