Q13030: velocity-envelope bypass in before-add-liquidity hook ordering when the pool is paused for swaps but LP withdrawals remain live

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity` with bin-local liquidity positions that sit exactly at a guard threshold while the pool is paused for swaps but LP withdrawals remain live, so that the per-block price-change cap is computed from stale or mis-squared values along `addLiquidity -> ExtensionCalling._beforeAddLiquidity -> LiquidityLib.addLiquidity`, corrupting the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting? Any deposit-side guard is only as strong as the argument binding and hook ordering on this public add-liquidity path. Use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_beforeAddLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity
- Attacker controls: bin-local liquidity positions that sit exactly at a guard threshold
- Exploit idea: Reach `addLiquidity -> ExtensionCalling._beforeAddLiquidity -> LiquidityLib.addLiquidity` in a live public flow and show that use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting. The exact value at risk is the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting.
- Invariant to test: Allowed price movement per block must remain within the exact configured squared-envelope check. The concrete assertion should cover the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting.
- Expected Immunefi impact: High if manipulated or stale prices can still reach live swaps.
- Fast validation: Assert add-liquidity hooks receive the same owner/salt/share vector that the pool later uses to mint shares and charge callback payment.
