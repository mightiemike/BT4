Q15661: wrong-actor binding in router and adder extension-data forwarding when the guard threshold is hit exactly after a small state-moving precursor action

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact* and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*` with multi-hop router execution where only some hops carry extension payloads while the guard threshold is hit exactly after a small state-moving precursor action, so that the hook checks the wrong actor among sender, owner, payer, or recipient along `public router or adder call -> forward extensionData to pool -> ExtensionCalling dispatches to live extensions`, corrupting which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes? The attacker controls extension payload distribution across public periphery steps, so forwarding mistakes turn into real bypasses. Separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol extensionData forwarding
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact* and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*
- Attacker controls: multi-hop router execution where only some hops carry extension payloads
- Exploit idea: Reach `public router or adder call -> forward extensionData to pool -> ExtensionCalling dispatches to live extensions` in a live public flow and show that separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate. The exact value at risk is which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes.
- Invariant to test: Every guard must key authorization to the same actor that the economic action is actually attributed to. The concrete assertion should cover which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes.
- Expected Immunefi impact: High direct loss or policy bypass on curated pools.
- Fast validation: Send distinct payloads per hop or per liquidity call and assert the correct extension receives exactly the intended bytes on-chain.
