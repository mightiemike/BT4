### Title
Cairo0 compiled-class cache in `StateReaderAndContractManager` skips the "still declared" re-check applied to Cairo1, letting a stale/reverted-block class be served as valid - ([File: crates/blockifier/src/state/state_reader_and_contract_manager.rs])

### Summary
`StateReaderAndContractManager::get_compiled_from_class_manager` explicitly re-validates cached Cairo1 (`RunnableCompiledClass::V1*`) classes against the canonical `is_declared` state before serving them from the process-wide `ContractClassManager` cache, precisely because "existence in the cache does not guarantee [declaration], it might contain a declared class from a reverted block." Cairo0 (`RunnableCompiledClass::V0`) classes are explicitly exempted from this same check (`RunnableCompiledClass::V0(_) => {}`), so a Cairo0 class that entered the shared cache via a declare that is no longer part of the canonical chain (reverted/rewound block, or a losing candidate block during multi-sequencer/consensus competition) will still be served as a valid, executable class on any subsequent `get_compiled_class` call, with no comparison to the authoritative "is this class hash currently declared" state. [1](#0-0) 

### Finding Description
`ContractClassManager` is a long-lived, cross-block, in-process cache keyed only by `class_hash` — it is not reset per block and not tied to a specific state root. `get_compiled_from_class_manager` is the single gatekeeper used by `StateReader::get_compiled_class` for every class read during transaction validation/execution (`crates/blockifier/src/state/state_reader_and_contract_manager.rs:151`). The comment on lines 76-78 acknowledges the exact bug class from the analog report — cached "authorization" (here: "this class is declared") can outlive the event that granted it (a declare transaction's block being reverted/not finalized) — and the code was fixed for Cairo1 classes by calling `self.state_reader.is_declared(class_hash)` before trusting the cache hit (lines 79-81). For Cairo0 the branch is a no-op (`RunnableCompiledClass::V0(_) => {}` at line 74), so the cache hit is returned unconditionally without ever asking the state reader whether the class hash is still declared at the current state.

This mirrors the FOSSBilling defect precisely at the abstraction level required by the scan rules: a decision ("this account/class is valid to use") is cached once and never re-validated against the authoritative source of truth when that source of truth can change (block revert/rewind vs. account suspension). `FetchCompiledClasses::is_declared` itself documents that "Cairo 0 classes always return `false`" (line 19-21 of the same file), which is presumably why the Cairo0 branch was left unchecked — but that means there is no re-validation path for Cairo0 at all, not that Cairo0 classes are somehow exempt from becoming stale.

### Impact Explanation
If a Cairo0 class hash is compiled and cached (e.g., via a Declare transaction included in a block that is subsequently reverted, rewound, or superseded by a different sequencer's competing block containing a different state), any later transaction referencing that class hash (via Deploy/DeployAccount constructor, class-hash lookup for execution, or a `library_call`/`replace_class` target) will be served the stale cached bytecode and treated as declared, even though the canonical chain state says the class hash was never (or is no longer) declared. This breaks the invariant that only currently-declared classes are executable, and can cause:
- Execution of a contract class the current committed state does not recognize as declared, producing state transitions/state diffs that a re-executing honest node (which queries `is_declared`/underlying storage rather than this warm cache) would reject or compute differently — an honest-node divergence in committed state root.
- Potential asset/logic exploitation if the stale class was intentionally crafted before being "reverted," and its behavior differs from what would be permitted under the actually-declared state (e.g., a class hash colliding conceptually with a later, differently-declared use, though a full hash collision is not required for the divergence risk — merely re-serving a class hash that is currently undeclared is already an unauthorized-state-use bug).

Because compiled-class validity gates whether a Deploy/DeployAccount/Invoke/L1Handler transaction is even executable, this is reachable from ordinary user-submitted transactions (declare followed by transactions using the class hash) combined with a benign reorg/rewind — no malicious operator/proposer collusion is required, only the ordinary possibility of block reversion that the code's own comment anticipates.

### Likelihood Explanation
The triggering condition (a block being reverted/rewound after a declare was processed and cached) is an explicitly anticipated, non-adversarial occurrence per the existing comment for the Cairo1 branch — the fix was already made for Cairo1 for exactly this reason. Cairo0 classes are still in active use in the codebase and test fixtures (e.g., `crates/blockifier_test_utils/resources/feature_contracts/cairo0/*`), so the vulnerable path is live, not dead code. The only missing piece is that the same guard was never extended to `RunnableCompiledClass::V0`.

### Recommendation
Apply the same re-validation to the `RunnableCompiledClass::V0` branch: extend `FetchCompiledClasses::is_declared` (or add a Cairo0-aware variant) so it can positively confirm current declaration status for deprecated classes too, and call it before returning a cached `V0` compiled class, mirroring the existing Cairo1 check at [2](#0-1) . At minimum, remove the blanket `RunnableCompiledClass::V0(_) => {}` bypass and route Cairo0 through a declared-state check backed by the current state reader rather than the long-lived cache.

### Proof of Concept
1. Submit a Declare (v0/v1, Cairo0 class) transaction; it gets included and executed in block N by the sequencer, and `ContractClassManager` caches the compiled Cairo0 class keyed by its `class_hash`.
2. Block N is reverted/rewound (e.g., due to a consensus rewind, or a competing sequencer's block for the same height being finalized instead, or a purposely triggered chain reorg in a multi-sequencer setting) such that the class hash is no longer declared in the canonical state (verifiable via the underlying `is_declared`/storage-backed check, which for Cairo0 the code never calls).
3. A subsequent transaction (e.g., a DeployAccount/Deploy or Invoke referencing that class hash) is submitted. `get_compiled_from_class_manager` finds the class hash in the `ContractClassManager` cache, matches `RunnableCompiledClass::V0(_)`, takes the no-op branch, and returns the stale class as valid without ever calling `is_declared`, at [3](#0-2) .
4. The transaction executes against a class hash that current canonical state does not recognize as declared, producing a state diff/commitment that a node re-deriving state strictly from the state reader (bypassing the stale in-memory cache) would not produce — a divergence in the computed state root/block hash.

### Citations

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L65-88)
```rust
impl<S: FetchCompiledClasses> StateReaderAndContractManager<S> {
    fn get_compiled_from_class_manager(
        &self,
        class_hash: ClassHash,
    ) -> StateResult<RunnableCompiledClass> {
        if let Some(runnable_class) =
            self.contract_class_manager.get_runnable(&class_hash, &self.native_classes_whitelist)
        {
            match &runnable_class {
                RunnableCompiledClass::V0(_) => {}
                _ => {
                    // The Cairo1 class is cached; verify it is declared,
                    // since existence in the cache does not guarantee that
                    // (it might contain a declared class from a reverted block, for example).
                    if !self.state_reader.is_declared(class_hash)? {
                        return Err(StateError::UndeclaredClassHash(class_hash));
                    }
                }
            }
            self.increment_cache_hit_metric();
            self.update_native_metrics(&runnable_class);
            return Ok(runnable_class);
        }
        self.increment_cache_miss_metric();
```
