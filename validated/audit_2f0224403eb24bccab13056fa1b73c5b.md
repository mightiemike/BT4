This is the key finding: `LocalClassManagerServer` is a `ConcurrentLocalComponentServer` [1](#0-0) , meaning `ClassManager::handle_request` (and therefore `add_class`) can run **concurrently** for multiple in-flight `AddClass` requests, unlike the mempool's `LocalMempoolServer` which is a plain `LocalComponentServer` processing requests sequentially. This matches the CVE-2023-32250 bug class: concurrent handlers operating on a shared object with no locking around the check-then-act sequence.

### Title
Missing synchronization in concurrent `ClassManager::add_class` allows a race on class registration - (File: `crates/apollo_class_manager/src/class_manager.rs`)

### Summary
`ClassManager` is served by a `ConcurrentLocalComponentServer`, so multiple `AddClass` (declare) requests for the same class hash can be processed by `&mut self.add_class()` concurrently across async tasks/threads. `add_class` performs a check on `self.classes.get_executable_class_hash_v2(class_hash)` and, if absent, proceeds to compile and later call `self.classes.set_class(...)`, with no mutex/lock guarding this "check-then-compile-then-set" critical section.

### Finding Description
`add_class` does:
1. Compute `class_hash`.
2. Check-if-exists via `self.classes.get_executable_class_hash_v2(class_hash)` [2](#0-1) .
3. If not found, call the (async, await-yielding) Sierra→CASM compiler [3](#0-2) .
4. Validate and call `self.classes.set_class(...)` to persist to storage and populate the in-memory caches [4](#0-3) .

`set_class` itself repeats the same non-atomic pattern: check `class_cached`, write to storage, then populate three separate caches (`classes`, `executable_classes`, `executable_class_hashes_v2`) in sequence, explicitly commented as "does not require atomicity" [5](#0-4) .

Because the server is `ConcurrentLocalComponentServer` [1](#0-0) , two `add_class` invocations for the same `class_hash` (submitted by an unprivileged declare-transaction sender, since a class can be declared/broadcast independently of gateway rate-limits, or replayed via p2p/gateway concurrently) can both pass the "not yet cached" check in step 2 before either completes step 4. Both then independently invoke the compiler and independently write to `set_class`. Between the storage write and the sequential cache `.set()` calls in `set_class` (classes → executable_classes → executable_class_hashes_v2, the last acting as "existence marker" per the comment), a concurrent reader (`get_executable`/`get_executable_class_hash_v2`) or a second racing writer can observe a partially-updated state: e.g. `executable_class_hashes_v2` marks the class as existing while `executable_classes` has not yet been populated by the *same* writer, or one writer's compiled CASM output overwrites another's mid-flight without any exclusion.

This is the direct structural analog of CVE-2023-32250: a shared per-object state machine (there, per-SMB-session; here, per-class-hash entry in the class cache/storage) is mutated by a check-then-act sequence with no lock, and the object is reachable by an unprivileged network-facing request handler that can be invoked concurrently for the same key.

### Impact Explanation
A successful race can cause a node to serve or persist an executable class (CASM) that does not correspond to the state the rest of the network derives for that same Sierra class hash, or cause `get_executable`/`get_executable_class_hash_v2` to return `None`/inconsistent data for a class hash that other, honestly-serialized nodes already resolve consistently. Since the class manager result feeds transaction execution (`get_compiled_class`) and ultimately the Starknet OS re-execution and state commitment, a persistent inconsistency here can lead to honest-node execution divergence for declare/invoke transactions referencing the raced class hash, i.e., wrong computed state root for one node relative to consensus, or reference to a missing/incorrect compiled class during execution.

### Likelihood Explanation
Requires simply submitting the same (or two distinct but colliding-in-timing) declare transactions for the same class hash to the gateway in close succession — no special privileges beyond being a normal declare-transaction sender needed. However, the actual data-race window is narrow (in-memory `set()` calls are fast), and much of the impact is bounded by the fact that `set_class` writes the *same* deterministic compiled output for a given class hash content, so most races are read/observe-order issues rather than divergent content. Exploitability to actually reach a wrong-root/consensus-divergence outcome is not proven with certainty from static review alone — this needs a Devin agent with test/execution access to construct a reliable reproduction confirming an actual externally observable state corruption versus a harmless transient race.

### Recommendation
Guard the class-manager's check-then-compile-then-write critical section (in `ClassManager::add_class` and `CachedClassStorage::set_class`) with a per-class-hash lock (e.g., a `DashMap<ClassHash, Mutex<()>>` or a single mutex covering the compile+cache-population sequence), and make the three-cache population in `set_class` atomic with respect to concurrent readers (e.g., populate `executable_class_hashes_v2` only after `executable_classes` and `classes` are fully populated, under the same lock used by the check).

### Proof of Concept
1. Run the node with `ClassManagerConfig` such that `ClassManager` is exposed via `LocalClassManagerServer` (`ConcurrentLocalComponentServer`) as configured in the default deployment [6](#0-5) .
2. As an unprivileged client, submit two declare-transaction-equivalent `AddClass` requests carrying the identical Sierra class (same `class_hash`) at effectively the same time, e.g., via two parallel gateway declare submissions that both reach the class manager before either's compilation completes.
3. Observe via `get_executable_class_hash_v2`/`get_executable` calls issued concurrently mid-race that one caller sees a "declared" marker (`executable_class_hashes_v2` populated) while `get_executable` still returns `None` (since `executable_classes`/`classes` from the *other* concurrent writer haven't landed yet), demonstrating the non-atomic, unlocked multi-field state transition described in `set_class`'s own comment [5](#0-4) .

### Citations

**File:** crates/apollo_class_manager/src/communication.rs (L14-21)
```rust
pub type LocalClassManagerServer =
    ConcurrentLocalComponentServer<ClassManager, ClassManagerRequest, ClassManagerResponse>;
pub type RemoteClassManagerServer =
    RemoteComponentServer<ClassManagerRequest, ClassManagerResponse>;

#[async_trait]
impl ComponentRequestHandler<ClassManagerRequest, ClassManagerResponse> for ClassManager {
    async fn handle_request(&mut self, request: ClassManagerRequest) -> ClassManagerResponse {
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L71-79)
```rust
    pub async fn add_class(&mut self, class: RawClass) -> ClassManagerResult<ClassHashes> {
        let sierra_class = SierraContractClass::try_from(&class)?;
        let class_hash = sierra_class.calculate_class_hash();
        if let Ok(Some(executable_class_hash_v2)) =
            self.classes.get_executable_class_hash_v2(class_hash)
        {
            // Class already exists.
            return Ok(ClassHashes { class_hash, executable_class_hash_v2 });
        }
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L81-90)
```rust
        let compilation_start_time = Instant::now();
        let (raw_executable_class, executable_class_hash_v2) =
            self.compiler.compile(class.clone()).await.map_err(|err| match err {
                SierraCompilerClientError::SierraCompilerError(error) => {
                    ClassManagerError::SierraCompiler { class_hash, error }
                }
                SierraCompilerClientError::ClientError(error) => {
                    ClassManagerError::Client(error.to_string())
                }
            })?;
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L102-109)
```rust
        self.validate_class_length(&raw_executable_class)?;
        Self::validate_class_version(&sierra_class)?;
        self.classes.set_class(
            class_hash,
            class,
            executable_class_hash_v2,
            raw_executable_class,
        )?;
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L128-136)
```rust

        // Cache the class.
        // Done after successfully writing to storage as an optimization;
        // does not require atomicity.
        self.classes.set(class_id, class);
        self.executable_classes.set(class_id, executable_class);
        // Cache the executable class hash last; acts as an existence marker.
        self.executable_class_hashes_v2.set(class_id, executable_class_hash_v2);

```
