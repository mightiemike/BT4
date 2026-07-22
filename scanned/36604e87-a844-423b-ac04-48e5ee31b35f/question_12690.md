Q12690: before-after inconsistency in after-swap hook ordering when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public `addLiquidity` or `addLiquidityWeighted` calls with owner/payer separation while the guarded operation touches only the active bin and one adjacent bin, so that the before-hook and after-hook observe materially different transaction context than the pool actually settled along `swap -> settle deltas -> ExtensionCalling._afterSwap -> post-state guard checks`, corrupting packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state? Post-trade protections like stop-loss rely on this hook seeing the same final state the pool already committed. Force a boundary case where post-state checks rely on an initial snapshot or direction flag that no longer matches the realized execution.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_afterSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public `addLiquidity` or `addLiquidityWeighted` calls with owner/payer separation
- Exploit idea: Reach `swap -> settle deltas -> ExtensionCalling._afterSwap -> post-state guard checks` in a live public flow and show that force a boundary case where post-state checks rely on an initial snapshot or direction flag that no longer matches the realized execution. The exact value at risk is packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state.
- Invariant to test: Before/after hooks must agree with the same settled trade or liquidity action the pool committed on-chain. The concrete assertion should cover packed slot0 before/after snapshots, swap deltas, protocol-fee amount, and the extension's view of the final state.
- Expected Immunefi impact: Medium/High if post-state protections can be bypassed on real user flows.
- Fast validation: Assert every after-swap hook sees the exact final slot0 and delta values committed by the pool and cannot be skipped or misordered for public swaps.
