Q13397: watermark boundary bug in deposit allowlist gate when the router forwards different extension payloads across otherwise connected hops

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*` with multi-hop router execution where only some hops carry extension payloads while the router forwards different extension payloads across otherwise connected hops, so that the stop-loss threshold is hit but the public swap still executes because the boundary math fails open along `public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner`, corrupting the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares? The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate. Move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction.

Target
- File/function: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*
- Attacker controls: multi-hop router execution where only some hops carry extension payloads
- Exploit idea: Reach `public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner` in a live public flow and show that move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction. The exact value at risk is the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares.
- Invariant to test: Stop-loss protection must fail closed at every watermark boundary that should block further value leakage. The concrete assertion should cover the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares.
- Expected Immunefi impact: High bad-price or value-leak execution that the configured protection was supposed to stop.
- Fast validation: Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.
