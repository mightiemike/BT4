Q13428: velocity-envelope bypass in deposit allowlist gate when the pool is paused for swaps but LP withdrawals remain live

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*` with two-transaction public sequences that move the pool just before the guarded call while the pool is paused for swaps but LP withdrawals remain live, so that the per-block price-change cap is computed from stale or mis-squared values along `public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner`, corrupting the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares? The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate. Use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting.

Target
- File/function: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*
- Attacker controls: two-transaction public sequences that move the pool just before the guarded call
- Exploit idea: Reach `public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner` in a live public flow and show that use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting. The exact value at risk is the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares.
- Invariant to test: Allowed price movement per block must remain within the exact configured squared-envelope check. The concrete assertion should cover the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares.
- Expected Immunefi impact: High if manipulated or stale prices can still reach live swaps.
- Fast validation: Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.
