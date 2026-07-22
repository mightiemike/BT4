Q13736: allowlist bypass in swap allowlist gate when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*` with alternating swap directions across consecutive blocks to stress rolling observations while the guarded operation touches only the active bin and one adjacent bin, so that a curated pool's allowlist can be bypassed through a public router or liquidity-adder path along `public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender`, corrupting the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity? Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting. Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.

Target
- File/function: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*
- Attacker controls: alternating swap directions across consecutive blocks to stress rolling observations
- Exploit idea: Reach `public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender` in a live public flow and show that enter through the supported periphery path rather than the direct pool call and see whether the identity check changes. The exact value at risk is the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity.
- Invariant to test: A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it. The concrete assertion should cover the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity.
- Expected Immunefi impact: High direct loss or curation failure if disallowed users can still trade or deposit.
- Fast validation: Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.
