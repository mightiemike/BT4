Q14993: watermark boundary bug in price velocity guard when the router forwards different extension payloads across otherwise connected hops

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public `swap` calls through the pool or router with crafted `extensionData` while the router forwards different extension payloads across otherwise connected hops, so that the stop-loss threshold is hit but the public swap still executes because the boundary math fails open along `swap -> beforeSwap hook -> mid-price derivation -> squared change check against last tracked mid price`, corrupting `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use? A public trader controls exact timing and direction, so any boundary bug in the per-block velocity formula is directly reachable. Move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction.

Target
- File/function: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol::beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public `swap` calls through the pool or router with crafted `extensionData`
- Exploit idea: Reach `swap -> beforeSwap hook -> mid-price derivation -> squared change check against last tracked mid price` in a live public flow and show that move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction. The exact value at risk is `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use.
- Invariant to test: Stop-loss protection must fail closed at every watermark boundary that should block further value leakage. The concrete assertion should cover `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use.
- Expected Immunefi impact: High bad-price or value-leak execution that the configured protection was supposed to stop.
- Fast validation: Vary block gaps and mid-price jumps across public swaps and assert no user can exceed the configured velocity envelope without reverting.
