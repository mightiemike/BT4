Q15256: wrong-actor binding in base extension pool identity when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}` with alternating swap directions across consecutive blocks to stress rolling observations while the guarded operation touches only the active bin and one adjacent bin, so that the hook checks the wrong actor among sender, owner, payer, or recipient along `public pool action -> extension callback entry -> BaseMetricExtension pool/factory identity checks`, corrupting which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted? Extensions are shared across pools, so the public swap or liquidity action must not let one pool bleed configuration into another. Separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate.

Target
- File/function: metric-periphery/contracts/extensions/base/BaseMetricExtension.sol::{onlyPool,onlyFactory}
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}
- Attacker controls: alternating swap directions across consecutive blocks to stress rolling observations
- Exploit idea: Reach `public pool action -> extension callback entry -> BaseMetricExtension pool/factory identity checks` in a live public flow and show that separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate. The exact value at risk is which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted.
- Invariant to test: Every guard must key authorization to the same actor that the economic action is actually attributed to. The concrete assertion should cover which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted.
- Expected Immunefi impact: High direct loss or policy bypass on curated pools.
- Fast validation: Deploy multiple pools using the same extension instance and assert no public action on one pool can pass onlyPool checks while consuming another pool's config.
