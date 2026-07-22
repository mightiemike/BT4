Q13481: before-after inconsistency in deposit allowlist gate when the extension set is active on a production-style pool with non-zero fees

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*` with public `swap` calls through the pool or router with crafted `extensionData` while the extension set is active on a production-style pool with non-zero fees, so that the before-hook and after-hook observe materially different transaction context than the pool actually settled along `public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner`, corrupting the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares? The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate. Force a boundary case where post-state checks rely on an initial snapshot or direction flag that no longer matches the realized execution.

Target
- File/function: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*
- Attacker controls: public `swap` calls through the pool or router with crafted `extensionData`
- Exploit idea: Reach `public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner` in a live public flow and show that force a boundary case where post-state checks rely on an initial snapshot or direction flag that no longer matches the realized execution. The exact value at risk is the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares.
- Invariant to test: Before/after hooks must agree with the same settled trade or liquidity action the pool committed on-chain. The concrete assertion should cover the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares.
- Expected Immunefi impact: Medium/High if post-state protections can be bypassed on real user flows.
- Fast validation: Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.
