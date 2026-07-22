Q12041: wrong-actor binding in before-swap hook ordering when the extension set is active on a production-style pool with non-zero fees

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public `swap` calls through the pool or router with crafted `extensionData` while the extension set is active on a production-style pool with non-zero fees, so that the hook checks the wrong actor among sender, owner, payer, or recipient along `swap -> ExtensionCalling._beforeSwap -> configured extension order -> SwapMath/pool execution`, corrupting hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement? Every real guard on the swap path depends on this dispatcher preserving the right order and arguments for public user calls. Separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_beforeSwap
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public `swap` calls through the pool or router with crafted `extensionData`
- Exploit idea: Reach `swap -> ExtensionCalling._beforeSwap -> configured extension order -> SwapMath/pool execution` in a live public flow and show that separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate. The exact value at risk is hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement.
- Invariant to test: Every guard must key authorization to the same actor that the economic action is actually attributed to. The concrete assertion should cover hook order, sender/recipient binding, bid/ask snapshot, price limit, and the exact extension payload consumed before settlement.
- Expected Immunefi impact: High direct loss or policy bypass on curated pools.
- Fast validation: Deploy pools with multiple live extensions and assert each before-swap hook receives the same sender, limit, bid/ask, and payload the pool used for settlement.
