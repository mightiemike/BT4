Q13535: paused-flow regression in deposit allowlist gate when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*` with permissionless pool creation that wires a legitimate extension set at deployment while the guarded operation touches only the active bin and one adjacent bin, so that a paused pool still exposes a public flow that an extension handles using active-swap assumptions along `public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner`, corrupting the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares? The attacker can separate payer from owner and can route through the liquidity adder, so the checked identity has to be exactly the one the pool intends to gate. Withdraw or otherwise interact during pause and look for an extension branch that assumes swaps are still active.

Target
- File/function: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol::beforeAddLiquidity
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::addLiquidity and metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol::addLiquidity*
- Attacker controls: permissionless pool creation that wires a legitimate extension set at deployment
- Exploit idea: Reach `public liquidity flow -> beforeAddLiquidity hook -> allowAll/allowedDepositor lookup keyed by pool and owner` in a live public flow and show that withdraw or otherwise interact during pause and look for an extension branch that assumes swaps are still active. The exact value at risk is the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares.
- Invariant to test: Pause semantics must remain coherent across core logic and every active extension callback. The concrete assertion should cover the identity actually checked against the allowlist and whether a disallowed depositor can still mint LP shares.
- Expected Immunefi impact: Medium broken core functionality or constrained loss of LP funds.
- Fast validation: Exercise direct pool adds and liquidity-adder adds with mismatched owner/payer pairs and assert the allowlist always gates the economically relevant depositor.
