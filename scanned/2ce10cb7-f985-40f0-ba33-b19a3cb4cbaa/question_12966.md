Q12966: watermark boundary bug in before-add-liquidity hook ordering when the extension set is active on a production-style pool with non-zero fees

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity` with bin-local liquidity positions that sit exactly at a guard threshold while the extension set is active on a production-style pool with non-zero fees, so that the stop-loss threshold is hit but the public swap still executes because the boundary math fails open along `addLiquidity -> ExtensionCalling._beforeAddLiquidity -> LiquidityLib.addLiquidity`, corrupting the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting? Any deposit-side guard is only as strong as the argument binding and hook ordering on this public add-liquidity path. Move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_beforeAddLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity
- Attacker controls: bin-local liquidity positions that sit exactly at a guard threshold
- Exploit idea: Reach `addLiquidity -> ExtensionCalling._beforeAddLiquidity -> LiquidityLib.addLiquidity` in a live public flow and show that move the pool metric exactly onto a drawdown or decay boundary and then attempt the loss-making direction. The exact value at risk is the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting.
- Invariant to test: Stop-loss protection must fail closed at every watermark boundary that should block further value leakage. The concrete assertion should cover the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting.
- Expected Immunefi impact: High bad-price or value-leak execution that the configured protection was supposed to stop.
- Fast validation: Assert add-liquidity hooks receive the same owner/salt/share vector that the pool later uses to mint shares and charge callback payment.
