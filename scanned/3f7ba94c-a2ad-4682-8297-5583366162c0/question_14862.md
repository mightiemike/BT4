Q14862: wrong-actor binding in price velocity guard when the guard threshold is hit exactly after a small state-moving precursor action

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with bin-local liquidity positions that sit exactly at a guard threshold while the guard threshold is hit exactly after a small state-moving precursor action, so that the hook checks the wrong actor among sender, owner, payer, or recipient along `swap -> beforeSwap hook -> mid-price derivation -> squared change check against last tracked mid price`, corrupting `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use? A public trader controls exact timing and direction, so any boundary bug in the per-block velocity formula is directly reachable. Separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate.

Target
- File/function: metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol::beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: bin-local liquidity positions that sit exactly at a guard threshold
- Exploit idea: Reach `swap -> beforeSwap hook -> mid-price derivation -> squared change check against last tracked mid price` in a live public flow and show that separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate. The exact value at risk is `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use.
- Invariant to test: Every guard must key authorization to the same actor that the economic action is actually attributed to. The concrete assertion should cover `lastMidPriceX64`, `lastUpdateBlock`, `maxChangePerBlockE18`, and the mid-price the next public swap is allowed to use.
- Expected Immunefi impact: High direct loss or policy bypass on curated pools.
- Fast validation: Vary block gaps and mid-price jumps across public swaps and assert no user can exceed the configured velocity envelope without reverting.
