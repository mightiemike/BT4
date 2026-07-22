Q15933: paused-flow regression in router and adder extension-data forwarding when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact* and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*` with multi-hop router execution where only some hops carry extension payloads while the guarded operation touches only the active bin and one adjacent bin, so that a paused pool still exposes a public flow that an extension handles using active-swap assumptions along `public router or adder call -> forward extensionData to pool -> ExtensionCalling dispatches to live extensions`, corrupting which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes? The attacker controls extension payload distribution across public periphery steps, so forwarding mistakes turn into real bypasses. Withdraw or otherwise interact during pause and look for an extension branch that assumes swaps are still active.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol extensionData forwarding
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact* and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*
- Attacker controls: multi-hop router execution where only some hops carry extension payloads
- Exploit idea: Reach `public router or adder call -> forward extensionData to pool -> ExtensionCalling dispatches to live extensions` in a live public flow and show that withdraw or otherwise interact during pause and look for an extension branch that assumes swaps are still active. The exact value at risk is which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes.
- Invariant to test: Pause semantics must remain coherent across core logic and every active extension callback. The concrete assertion should cover which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes.
- Expected Immunefi impact: Medium broken core functionality or constrained loss of LP funds.
- Fast validation: Send distinct payloads per hop or per liquidity call and assert the correct extension receives exactly the intended bytes on-chain.
