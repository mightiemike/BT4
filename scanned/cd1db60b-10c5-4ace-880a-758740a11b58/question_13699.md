Q13699: payload mismatch in swap allowlist gate when the guard threshold is hit exactly after a small state-moving precursor action

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*` with remove-liquidity calls while the pool is paused and extensions still observe state while the guard threshold is hit exactly after a small state-moving precursor action, so that extension payload bytes are valid but delivered to the wrong hop or wrong liquidity operation along `public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender`, corrupting the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity? Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting. Use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step.

Target
- File/function: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*
- Attacker controls: remove-liquidity calls while the pool is paused and extensions still observe state
- Exploit idea: Reach `public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender` in a live public flow and show that use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step. The exact value at risk is the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity.
- Invariant to test: Each extension invocation must consume only the bytes intended for that exact public action. The concrete assertion should cover the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity.
- Expected Immunefi impact: High if a required guard can be bypassed through wrong payload routing.
- Fast validation: Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.
