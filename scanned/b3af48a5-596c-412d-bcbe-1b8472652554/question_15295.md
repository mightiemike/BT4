Q15295: payload mismatch in base extension pool identity when the guarded operation touches only the active bin and one adjacent bin

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}` with permissionless pool creation that wires a legitimate extension set at deployment while the guarded operation touches only the active bin and one adjacent bin, so that extension payload bytes are valid but delivered to the wrong hop or wrong liquidity operation along `public pool action -> extension callback entry -> BaseMetricExtension pool/factory identity checks`, corrupting which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted? Extensions are shared across pools, so the public swap or liquidity action must not let one pool bleed configuration into another. Use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step.

Target
- File/function: metric-periphery/contracts/extensions/base/BaseMetricExtension.sol::{onlyPool,onlyFactory}
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}
- Attacker controls: permissionless pool creation that wires a legitimate extension set at deployment
- Exploit idea: Reach `public pool action -> extension callback entry -> BaseMetricExtension pool/factory identity checks` in a live public flow and show that use distinct public payloads across steps and see whether a guard consumes bytes intended for a different step. The exact value at risk is which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted.
- Invariant to test: Each extension invocation must consume only the bytes intended for that exact public action. The concrete assertion should cover which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted.
- Expected Immunefi impact: High if a required guard can be bypassed through wrong payload routing.
- Fast validation: Deploy multiple pools using the same extension instance and assert no public action on one pool can pass onlyPool checks while consuming another pool's config.
