Q13638: hook skip or reorder in swap allowlist gate when the router forwards different extension payloads across otherwise connected hops

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*` with bin-local liquidity positions that sit exactly at a guard threshold while the router forwards different extension payloads across otherwise connected hops, so that a configured extension hook is reachable but executes in the wrong order or not at all along `public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender`, corrupting the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity? Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting. Use a public action that traverses multiple extensions and look for an ordering gap that changes the effective protection boundary.

Target
- File/function: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*
- Attacker controls: bin-local liquidity positions that sit exactly at a guard threshold
- Exploit idea: Reach `public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender` in a live public flow and show that use a public action that traverses multiple extensions and look for an ordering gap that changes the effective protection boundary. The exact value at risk is the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity.
- Invariant to test: Configured hooks must execute exactly once and in the validated order on every live user flow. The concrete assertion should cover the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity.
- Expected Immunefi impact: High direct loss if a protection hook silently fails open.
- Fast validation: Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.
