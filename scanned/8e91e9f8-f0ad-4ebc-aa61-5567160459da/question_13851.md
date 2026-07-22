Q13851: cross-pool config bleed in swap allowlist gate when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*` with remove-liquidity calls while the pool is paused and extensions still observe state while the guarded operation touches only the active bin and one adjacent bin, so that shared extension instances let one pool's configuration affect another pool's public flow along `public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender`, corrupting the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity? Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting. Use public actions on one pool after another pool has updated shared extension storage or observations.

Target
- File/function: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*
- Attacker controls: remove-liquidity calls while the pool is paused and extensions still observe state
- Exploit idea: Reach `public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender` in a live public flow and show that use public actions on one pool after another pool has updated shared extension storage or observations. The exact value at risk is the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity.
- Invariant to test: Extension state must be namespaced by pool strongly enough that one pool cannot authorize or disable another pool's protections. The concrete assertion should cover the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity.
- Expected Immunefi impact: High if a public user can exploit one pool's config to break another pool's safety boundary.
- Fast validation: Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.
