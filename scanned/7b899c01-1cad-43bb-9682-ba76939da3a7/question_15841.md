Q15841: cross-pool config bleed in router and adder extension-data forwarding when the extension set is active on a production-style pool with non-zero fees

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact* and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*` with public `swap` calls through the pool or router with crafted `extensionData` while the extension set is active on a production-style pool with non-zero fees, so that shared extension instances let one pool's configuration affect another pool's public flow along `public router or adder call -> forward extensionData to pool -> ExtensionCalling dispatches to live extensions`, corrupting which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes? The attacker controls extension payload distribution across public periphery steps, so forwarding mistakes turn into real bypasses. Use public actions on one pool after another pool has updated shared extension storage or observations.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol extensionData forwarding
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact* and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*
- Attacker controls: public `swap` calls through the pool or router with crafted `extensionData`
- Exploit idea: Reach `public router or adder call -> forward extensionData to pool -> ExtensionCalling dispatches to live extensions` in a live public flow and show that use public actions on one pool after another pool has updated shared extension storage or observations. The exact value at risk is which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes.
- Invariant to test: Extension state must be namespaced by pool strongly enough that one pool cannot authorize or disable another pool's protections. The concrete assertion should cover which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes.
- Expected Immunefi impact: High if a public user can exploit one pool's config to break another pool's safety boundary.
- Fast validation: Send distinct payloads per hop or per liquidity call and assert the correct extension receives exactly the intended bytes on-chain.
