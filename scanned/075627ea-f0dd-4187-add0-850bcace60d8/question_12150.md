Q12150: allowlist bypass in before-swap hook ordering when the pool is paused for swaps but LP withdrawals remain live

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with bin-local liquidity positions that sit exactly at a guard threshold while the pool is paused for swaps but LP withdrawals remain live, so that a curated pool's allowlist can be bypassed through a public router or liquidity-adder path along `swap -> ExtensionCalling._beforeSwap -> configured extension order -> SwapMath/pool execution`, corrupting hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement? Every real guard on the swap path depends on this dispatcher preserving the right order and arguments for public user calls. Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: bin-local liquidity positions that sit exactly at a guard threshold
- Exploit idea: Reach `swap -> ExtensionCalling._beforeSwap -> configured extension order -> SwapMath/pool execution` in a live public flow and show that enter through the supported periphery path rather than the direct pool call and see whether the identity check changes. The exact value at risk is hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement.
- Invariant to test: A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it. The concrete assertion should cover hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement.
- Expected Immunefi impact: High direct loss or curation failure if disallowed users can still trade or deposit.
- Fast validation: Deploy pools with multiple live extensions and assert each before-swap hook receives the same sender, limit, bid/ask, and payload the pool used for settlement.
