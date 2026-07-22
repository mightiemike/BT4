Q12120: payload mismatch in before-swap hook ordering when the router forwards different extension payloads across otherwise connected hops

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with alternating swap directions across consecutive blocks to stress rolling observations while the router forwards different extension payloads across otherwise connected hops, so that extension payload bytes are valid but delivered to the wrong hop or wrong liquidity operation along `swap -> ExtensionCalling._beforeSwap -> configured extension order -> SwapMath/pool execution`, corrupting hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement? Every real guard on the swap path depends on this dispatcher preserving the right order and arguments for public user calls. Use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: alternating swap directions across consecutive blocks to stress rolling observations
- Exploit idea: Reach `swap -> ExtensionCalling._beforeSwap -> configured extension order -> SwapMath/pool execution` in a live public flow and show that use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step. The exact value at risk is hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement.
- Invariant to test: Each extension invocation must consume only the bytes intended for that exact public action. The concrete assertion should cover hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement.
- Expected Immunefi impact: High if a required guard can be bypassed through wrong payload routing.
- Fast validation: Deploy pools with multiple live extensions and assert each before-swap hook receives the same sender, limit, bid/ask, and payload the pool used for settlement.
