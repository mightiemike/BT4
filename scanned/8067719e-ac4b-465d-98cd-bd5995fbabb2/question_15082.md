Q15082: before-after inconsistency in price velocity guard when the extension set is active on a production-style pool with non-zero fees

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public `addLiquidity` or `addLiquidityWeighted` calls with owner/payer separation while the extension set is active on a production-style pool with non-zero fees, so that the before-hook and after-hook observe materially different transaction context than the pool actually settled along `swap -> beforeSwap hook -> mid-price derivation -> squared change check against last tracked mid price`, corrupting `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use? A public trader controls exact timing and direction, so any boundary bug in the per-block velocity formula is directly reachable. Force a boundary case where post-state checks rely on an initial snapshot or direction flag that no longer matches the realized execution.

Target
- File/function: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol::beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public `addLiquidity` or `addLiquidityWeighted` calls with owner/payer separation
- Exploit idea: Reach `swap -> beforeSwap hook -> mid-price derivation -> squared change check against last tracked mid price` in a live public flow and show that force a boundary case where post-state checks rely on an initial snapshot or direction flag that no longer matches the realized execution. The exact value at risk is `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use.
- Invariant to test: Before/after hooks must agree with the same settled trade or liquidity action the pool committed on-chain. The concrete assertion should cover `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use.
- Expected Immunefi impact: Medium/High if post-state protections can be bypassed on real user flows.
- Fast validation: Vary block gaps and mid-price jumps across public swaps and assert no user can exceed the configured velocity envelope without reverting.
