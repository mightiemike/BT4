Q13474: cross-pool config bleed in deposit allowlist gate when the router forwards different extension payloads across otherwise connected hops

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*` with public `addLiquidity` or `addLiquidityWeighted` calls with owner/payer separation while the router forwards different extension payloads across otherwise connected hops, so that shared extension instances let one pool's configuration affect another pool's public flow along `public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner`, corrupting the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares? The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate. Use public actions on one pool after another pool has updated shared extension storage or observations.

Target
- File/function: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*
- Attacker controls: public `addLiquidity` or `addLiquidityWeighted` calls with owner/payer separation
- Exploit idea: Reach `public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner` in a live public flow and show that use public actions on one pool after another pool has updated shared extension storage or observations. The exact value at risk is the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares.
- Invariant to test: Extension state must be namespaced by pool strongly enough that one pool cannot authorize or disable another pool's protections. The concrete assertion should cover the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares.
- Expected Immunefi impact: High if a public user can exploit one pool's config to break another pool's safety boundary.
- Fast validation: Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.
