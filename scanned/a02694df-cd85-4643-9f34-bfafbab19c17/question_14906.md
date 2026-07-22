Q14906: payload mismatch in price velocity guard when the pool is paused for swaps but LP withdrawals remain live

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public `addLiquidity` or `addLiquidityWeighted` calls with owner/payer separation while the pool is paused for swaps but LP withdrawals remain live, so that extension payload bytes are valid but delivered to the wrong hop or wrong liquidity operation along `swap -> beforeSwap hook -> mid-price derivation -> squared change check against last tracked mid price`, corrupting `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use? A public trader controls exact timing and direction, so any boundary bug in the per-block velocity formula is directly reachable. Use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step.

Target
- File/function: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol::beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public `addLiquidity` or `addLiquidityWeighted` calls with owner/payer separation
- Exploit idea: Reach `swap -> beforeSwap hook -> mid-price derivation -> squared change check against last tracked mid price` in a live public flow and show that use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step. The exact value at risk is `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use.
- Invariant to test: Each extension invocation must consume only the bytes intended for that exact public action. The concrete assertion should cover `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use.
- Expected Immunefi impact: High if a required guard can be bypassed through wrong payload routing.
- Fast validation: Vary block gaps and mid-price jumps across public swaps and assert no user can exceed the configured velocity envelope without reverting.
