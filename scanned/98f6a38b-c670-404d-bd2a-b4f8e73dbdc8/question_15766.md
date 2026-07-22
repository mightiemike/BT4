Q15766: watermark boundary bug in router and adder extension-data forwarding when the extension set is active on a production-style pool with non-zero fees

Question
Can an unprivileged attacker enter through `metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact* and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*` with bin-local liquidity positions that sit exactly at a guard threshold while the extension set is active on a production-style pool with non-zero fees, so that the stop-loss threshold is hit but the public swap still executes because the boundary math fails open along `public router or adder call -> forward extensionData to pool -> ExtensionCalling dispatches to live extensions`, corrupting which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes? The attacker controls extension payload distribution across public periphery steps, so forwarding mistakes turn into real bypasses. Move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction.

Target
- File/function: metric-periphery/contracts/MetricOmmSimpleRouter.sol and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol extensionData forwarding
- Entrypoint: metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact* and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*
- Attacker controls: bin-local liquidity positions that sit exactly at a guard threshold
- Exploit idea: Reach `public router or adder call -> forward extensionData to pool -> ExtensionCalling dispatches to live extensions` in a live public flow and show that move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction. The exact value at risk is which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes.
- Invariant to test: Stop-loss protection must fail closed at every watermark boundary that should block further value leakage. The concrete assertion should cover which extension payload each hop or liquidity operation consumes and whether a required guard sees the intended bytes.
- Expected Immunefi impact: High bad-price or value-leak execution that the configured protection was supposed to stop.
- Fast validation: Send distinct payloads per hop or per liquidity call and assert the correct extension receives exactly the intended bytes on-chain.
