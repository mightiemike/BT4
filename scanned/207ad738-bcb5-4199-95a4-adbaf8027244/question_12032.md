Q12032: hook skip or reorder in before-swap hook ordering when the pool is paused for swaps but LP withdrawals remain live

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with alternating swap directions across consecutive blocks to stress rolling observations while the pool is paused for swaps but LP withdrawals remain live, so that a configured extension hook is reachable but executes in the wrong order or not at all along `swap -> ExtensionCalling._beforeSwap -> configured extension order -> SwapMath/pool execution`, corrupting hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement? Every real guard on the swap path depends on this dispatcher preserving the right order and arguments for public user calls. Use a public action that traverses multiple extensions and look for an ordering gap that changes the effective protection boundary.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: alternating swap directions across consecutive blocks to stress rolling observations
- Exploit idea: Reach `swap -> ExtensionCalling._beforeSwap -> configured extension order -> SwapMath/pool execution` in a live public flow and show that use a public action that traverses multiple extensions and look for an ordering gap that changes the effective protection boundary. The exact value at risk is hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement.
- Invariant to test: Configured hooks must execute exactly once and in the validated order on every live user flow. The concrete assertion should cover hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement.
- Expected Immunefi impact: High direct loss if a protection hook silently fails open.
- Fast validation: Deploy pools with multiple live extensions and assert each before-swap hook receives the same sender, limit, bid/ask, and payload the pool used for settlement.
