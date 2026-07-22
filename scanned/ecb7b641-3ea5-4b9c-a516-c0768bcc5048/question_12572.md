Q12572: watermark boundary bug in after-swap hook ordering when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with two-transaction public sequences that move the pool just before the guarded call while the guarded operation touches only the active bin and one adjacent bin, so that the stop-loss threshold is hit but the public swap still executes because the boundary math fails open along `swap -> settle deltas -> ExtensionCalling._afterSwap -> post-state guard checks`, corrupting packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state? Post-trade protections like stop-loss rely on this hook seeing the same final state the pool already committed. Move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_afterSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: two-transaction public sequences that move the pool just before the guarded call
- Exploit idea: Reach `swap -> settle deltas -> ExtensionCalling._afterSwap -> post-state guard checks` in a live public flow and show that move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction. The exact value at risk is packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state.
- Invariant to test: Stop-loss protection must fail closed at every watermark boundary that should block further value leakage. The concrete assertion should cover packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state.
- Expected Immunefi impact: High bad-price or value-leak execution that the configured protection was supposed to stop.
- Fast validation: Assert every after-swap hook sees the exact final slot0 and delta values committed by the pool and cannot be skipped or misordered for public swaps.
