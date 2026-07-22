Q12833: hook skip or reorder in before-add-liquidity hook ordering when the router forwards different extension payloads across otherwise connected hops

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity` with public `swap` calls through the pool or router with crafted `extensionData` while the router forwards different extension payloads across otherwise connected hops, so that a configured extension hook is reachable but executes in the wrong order or not at all along `addLiquidity -> ExtensionCalling._beforeAddLiquidity -> LiquidityLib.addLiquidity`, corrupting the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting? Any deposit-side guard is only as strong as the argument binding and hook ordering on this public add-liquidity path. Use a public action that traverses multiple extensions and look for an ordering gap that changes the effective protection boundary.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_beforeAddLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity
- Attacker controls: public `swap` calls through the pool or router with crafted `extensionData`
- Exploit idea: Reach `addLiquidity -> ExtensionCalling._beforeAddLiquidity -> LiquidityLib.addLiquidity` in a live public flow and show that use a public action that traverses multiple extensions and look for an ordering gap that changes the effective protection boundary. The exact value at risk is the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting.
- Invariant to test: Configured hooks must execute exactly once and in the validated order on every live user flow. The concrete assertion should cover the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting.
- Expected Immunefi impact: High direct loss if a protection hook silently fails open.
- Fast validation: Assert add-liquidity hooks receive the same owner/salt/share vector that the pool later uses to mint shares and charge callback payment.
