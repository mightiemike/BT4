Q13978: timed-threshold manipulation in swap allowlist gate when the guard threshold is hit exactly after a small state-moving precursor action

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*` with public `addLiquidity` or `addLiquidityWeighted` calls with owner/payer separation while the guard threshold is hit exactly after a small state-moving precursor action, so that a small public precursor action can place the next guarded trade just on the permissive side of a threshold that should have blocked it along `public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender`, corrupting the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity? Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting. Use two public transactions in sequence so the second one benefits from stale threshold state or observation updates.

Target
- File/function: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*
- Attacker controls: public `addLiquidity` or `addLiquidityWeighted` calls with owner/payer separation
- Exploit idea: Reach `public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender` in a live public flow and show that use two public transactions in sequence so the second one benefits from stale threshold state or observation updates. The exact value at risk is the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity.
- Invariant to test: Guard thresholds must update atomically enough that one public action cannot invalidate the safety assumptions of the next. The concrete assertion should cover the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity.
- Expected Immunefi impact: High if timing lets an attacker bypass a live loss-prevention control.
- Fast validation: Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.
