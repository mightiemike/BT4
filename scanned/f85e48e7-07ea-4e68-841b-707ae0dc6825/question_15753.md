Q15753: allowlist bypass in router and adder extension-data forwarding when the router forwards different extension payloads across otherwise connected hops

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact* and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*` with public `swap` calls through the pool or router with crafted `extensionData` while the router forwards different extension payloads across otherwise connected hops, so that a curated pool's allowlist can be bypassed through a public router or liquidity-adder path along `public router or adder call -> forward extensionData to pool -> ExtensionCalling dispatches to live extensions`, corrupting which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes? The attacker controls extension payload distribution across public periphery steps, so forwarding mistakes turn into real bypasses. Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol extensionData forwarding
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact* and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*
- Attacker controls: public `swap` calls through the pool or router with crafted `extensionData`
- Exploit idea: Reach `public router or adder call -> forward extensionData to pool -> ExtensionCalling dispatches to live extensions` in a live public flow and show that enter through the supported periphery path rather than the direct pool call and see whether the identity check changes. The exact value at risk is which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes.
- Invariant to test: A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it. The concrete assertion should cover which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes.
- Expected Immunefi impact: High direct loss or curation failure if disallowed users can still trade or deposit.
- Fast validation: Send distinct payloads per hop or per liquidity call and assert the correct extension receives exactly the intended bytes on-chain.
