Q12657: cross-pool config bleed in after-swap hook ordering when the guard threshold is hit exactly after a small state-moving precursor action

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public `swap` calls through the pool or router with crafted `extensionData` while the guard threshold is hit exactly after a small state-moving precursor action, so that shared extension instances let one pool's configuration affect another pool's public flow along `swap -> settle deltas -> ExtensionCalling._afterSwap -> post-state guard checks`, corrupting packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state? Post-trade protections like stop-loss rely on this hook seeing the same final state the pool already committed. Use public actions on one pool after another pool has updated shared extension storage or observations.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_afterSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public `swap` calls through the pool or router with crafted `extensionData`
- Exploit idea: Reach `swap -> settle deltas -> ExtensionCalling._afterSwap -> post-state guard checks` in a live public flow and show that use public actions on one pool after another pool has updated shared extension storage or observations. The exact value at risk is packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state.
- Invariant to test: Extension state must be namespaced by pool strongly enough that one pool cannot authorize or disable another pool's protections. The concrete assertion should cover packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state.
- Expected Immunefi impact: High if a public user can exploit one pool's config to break another pool's safety boundary.
- Fast validation: Assert every after-swap hook sees the exact final slot0 and delta values committed by the pool and cannot be skipped or misordered for public swaps.
