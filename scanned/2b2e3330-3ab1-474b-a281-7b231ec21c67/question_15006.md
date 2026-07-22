Q15006: velocity-envelope bypass in price velocity guard when the extension set is active on a production-style pool with non-zero fees

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with bin-local liquidity positions that sit exactly at a guard threshold while the extension set is active on a production-style pool with non-zero fees, so that the per-block price-change cap is computed from stale or mis-squared values along `swap -> beforeSwap hook -> mid-price derivation -> squared change check against last tracked mid price`, corrupting `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use? A public trader controls exact timing and direction, so any boundary bug in the per-block velocity formula is directly reachable. Use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting.

Target
- File/function: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol::beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: bin-local liquidity positions that sit exactly at a guard threshold
- Exploit idea: Reach `swap -> beforeSwap hook -> mid-price derivation -> squared change check against last tracked mid price` in a live public flow and show that use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting. The exact value at risk is `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use.
- Invariant to test: Allowed price movement per block must remain within the exact configured squared-envelope check. The concrete assertion should cover `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use.
- Expected Immunefi impact: High if manipulated or stale prices can still reach live swaps.
- Fast validation: Vary block gaps and mid-price jumps across public swaps and assert no user can exceed the configured velocity envelope without reverting.
