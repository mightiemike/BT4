Q15422: velocity-envelope bypass in base extension pool identity when the guard threshold is hit exactly after a small state-moving precursor action

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}` with bin-local liquidity positions that sit exactly at a guard threshold while the guard threshold is hit exactly after a small state-moving precursor action, so that the per-block price-change cap is computed from stale or mis-squared values along `public pool action -> extension callback entry -> BaseMetricExtension pool/factory identity checks`, corrupting which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted? Extensions are shared across pools, so the public swap or liquidity action must not let one pool bleed configuration into another. Use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting.

Target
- File/function: metric-periphery/contracts/extensions/base/BaseMetricExtension.sol::{onlyPool,onlyFactory}
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::{swap,addLiquidity,removeLiquidity}
- Attacker controls: bin-local liquidity positions that sit exactly at a guard threshold
- Exploit idea: Reach `public pool action -> extension callback entry -> BaseMetricExtension pool/factory identity checks` in a live public flow and show that use public timing across consecutive blocks so the actual mid-price jump exceeds the intended envelope without reverting. The exact value at risk is which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted.
- Invariant to test: Allowed price movement per block must remain within the exact configured squared-envelope check. The concrete assertion should cover which pool is authorized to call each extension and whether one pool can cause another pool's extension state to be consulted.
- Expected Immunefi impact: High if manipulated or stale prices can still reach live swaps.
- Fast validation: Deploy multiple pools using the same extension instance and assert no public action on one pool can pass onlyPool checks while consuming another pool's config.
