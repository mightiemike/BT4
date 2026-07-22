Q13115: before-after inconsistency in before-add-liquidity hook ordering when the router forwards different extension payloads across otherwise connected hops

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity` with remove-liquidity calls while the pool is paused and extensions still observe state while the router forwards different extension payloads across otherwise connected hops, so that the before-hook and after-hook observe materially different transaction context than the pool actually settled along `addLiquidity -> ExtensionCalling._beforeAddLiquidity -> LiquidityLib.addLiquidity`, corrupting the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting? Any deposit-side guard is only as strong as the argument binding and hook ordering on this public add-liquidity path. Force a boundary case where post-state checks rely on an initial snapshot or direction flag that no longer matches the realized execution.

Target
- File/function: metric-core/contracts/ExtensionCalling.sol::_beforeAddLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity
- Attacker controls: remove-liquidity calls while the pool is paused and extensions still observe state
- Exploit idea: Reach `addLiquidity -> ExtensionCalling._beforeAddLiquidity -> LiquidityLib.addLiquidity` in a live public flow and show that force a boundary case where post-state checks rely on an initial snapshot or direction flag that no longer matches the realized execution. The exact value at risk is the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting.
- Invariant to test: Before/after hooks must agree with the same settled trade or liquidity action the pool committed on-chain. The concrete assertion should cover the owner, salt, share vector, and extension payload that a deposit guard or accounting extension sees before minting.
- Expected Immunefi impact: Medium/High if post-state protections can be bypassed on real user flows.
- Fast validation: Assert add-liquidity hooks receive the same owner/salt/share vector that the pool later uses to mint shares and charge callback payment.
