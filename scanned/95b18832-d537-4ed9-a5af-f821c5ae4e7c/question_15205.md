Q15205: hook skip or reorder in base extension pool identity when the extension set is active on a production-style pool with non-zero fees

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}` with multi-hop router execution where only some hops carry extension payloads while the extension set is active on a production-style pool with non-zero fees, so that a configured extension hook is reachable but executes in the wrong order or not at all along `public pool action -> extension callback entry -> BaseMetricExtension pool/factory identity checks`, corrupting which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted? Extensions are shared across pools, so the public swap or liquidity action must not let one pool bleed configuration into another. Use a public action that traverses multiple extensions and look for an ordering gap that changes the effective protection boundary.

Target
- File/function: metric-periphery/contracts/extensions/base/BaseMetricExtension.sol::{onlyPool,onlyFactory}
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}
- Attacker controls: multi-hop router execution where only some hops carry extension payloads
- Exploit idea: Reach `public pool action -> extension callback entry -> BaseMetricExtension pool/factory identity checks` in a live public flow and show that use a public action that traverses multiple extensions and look for an ordering gap that changes the effective protection boundary. The exact value at risk is which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted.
- Invariant to test: Configured hooks must execute exactly once and in the validated order on every live user flow. The concrete assertion should cover which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted.
- Expected Immunefi impact: High direct loss if a protection hook silently fails open.
- Fast validation: Deploy multiple pools using the same extension instance and assert no public action on one pool can pass onlyPool checks while consuming another pool's config.
