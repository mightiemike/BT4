Q12920: payload mismatch in before-add-liquidity hook ordering when the router forwards different extension payloads across otherwise connected hops

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity` with alternating swap directions across consecutive blocks to stress rolling observations while the router forwards different extension payloads across otherwise connected hops, so that extension payload bytes are valid but delivered to the wrong hop or wrong liquidity operation along `addLiquidity -> ExtensionCalling._beforeAddLiquidity -> LiquidityLib.addLiquidity`, corrupting the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting? Any deposit-side guard is only as strong as the argument binding and hook ordering on this public add-liquidity path. Use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_beforeAddLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity
- Attacker controls: alternating swap directions across consecutive blocks to stress rolling observations
- Exploit idea: Reach `addLiquidity -> ExtensionCalling._beforeAddLiquidity -> LiquidityLib.addLiquidity` in a live public flow and show that use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step. The exact value at risk is the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting.
- Invariant to test: Each extension invocation must consume only the bytes intended for that exact public action. The concrete assertion should cover the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting.
- Expected Immunefi impact: High if a required guard can be bypassed through wrong payload routing.
- Fast validation: Assert add-liquidity hooks receive the same owner/salt/share vector that the pool later uses to mint shares and charge callback payment.
