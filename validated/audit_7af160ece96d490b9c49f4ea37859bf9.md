### Title
Panic (process crash) on constructor lookup for a Cairo 0 class missing the `CONSTRUCTOR` entry-point-type key - (File: `crates/blockifier/src/execution/contract_class.rs`)

### Summary
`CompiledClassV0::constructor_selector` accesses a `HashMap<EntryPointType, Vec<EntryPointV0>>` with the `[]` indexing operator instead of `.get()`. If the underlying deprecated (Cairo 0) contract class does not contain a `CONSTRUCTOR` key at all (as opposed to containing an empty vector for it), the index operation panics rather than returning `None`. This function sits directly in the constructor-execution path that runs on every contract deployment (`DEPLOY`/`DEPLOY_ACCOUNT` transactions and the `deploy` syscall), so a class shaped this way can crash the executing node process when deployed — analogous to the reported Bento4 `AP4_StsdAtom` null-pointer dereference, where malformed/incomplete input causes an unchecked access that terminates the process instead of failing gracefully.

### Finding Description
`CompiledClassV0::constructor_selector`: [1](#0-0) 
```
fn constructor_selector(&self) -> Option<EntryPointSelector> {
    Some(self.entry_points_by_type[&EntryPointType::Constructor].first()?.selector)
}
```
uses `HashMap`'s `Index` trait (`map[&key]`), which panics with `"no entry found for key"` if the key is absent — it does not gracefully degrade to `None` the way `.get(&key)` would.

`entry_points_by_type` for a deprecated (Cairo 0) contract class is deserialized as a plain `HashMap<EntryPointType, Vec<EntryPointV0>>` with no requirement that all three entry point types (`CONSTRUCTOR`, `EXTERNAL`, `L1_HANDLER`) be present as keys: [2](#0-1) 
```
pub struct ContractClass {
    #[serde(default, deserialize_with = "deserialize_optional_contract_class_abi_entry_vector")]
    pub abi: Option<Vec<ContractClassAbiEntry>>,
    pub program: Program,
    pub entry_points_by_type: HashMap<EntryPointType, Vec<EntryPointV0>>,
}
```
There is nothing in the deserialization path that inserts a default empty `Vec` for a missing `CONSTRUCTOR` key — a class JSON that simply omits the `"CONSTRUCTOR"` key (rather than emitting `"CONSTRUCTOR": []"`) is a structurally valid `ContractClass`.

This method is reached on every contract constructor execution: [3](#0-2) 
```
pub fn execute_constructor_entry_point(...) -> ConstructorEntryPointExecutionResult<CallInfo> {
    ...
    let compiled_class = state.get_compiled_class(ctor_context.class_hash)...?;
    let Some(constructor_selector) = compiled_class.constructor_selector() else {
        // Contract has no constructor.
        return handle_empty_constructor(...)
    };
    ...
}
```
`execute_constructor_entry_point` is invoked whenever a contract of that class is deployed — via a `DEPLOY_ACCOUNT` transaction or the `deploy` syscall issued by any other contract during normal execution of a user transaction. If the target class's `entry_points_by_type` map lacks a `CONSTRUCTOR` key, `constructor_selector()` panics instead of returning `None`.

### Impact Explanation
A panic inside transaction execution on the sequencer/validator node is not a clean, catchable `Result` error in this call chain (`Index` panics, not `Result`); depending on how the outer batcher/validator wraps execution, this can crash the executing worker/process or otherwise abort block building/validation for that transaction. If honest sequencers panic while a subset of implementations tolerate/reject the transaction differently (or if only some nodes crash while proposers using different code paths do not), this can produce a network unable to confirm new transactions or honest-node divergence for the affected class/deployment, satisfying the “concrete loss / network unable to confirm new transactions” criterion. At minimum it is a single-transaction/single-class denial-of-service against the deploying node.

### Likelihood Explanation
The trigger requires: (1) a Cairo 0 class whose `entry_points_by_type` JSON omits the `CONSTRUCTOR` key entirely (structurally valid per the loose `HashMap` deserialization, with no schema-level requirement to include all three keys), and (2) any transaction that deploys a contract of that class hash (`DEPLOY_ACCOUNT` or `deploy` syscall) — both of which are directly reachable by an unprivileged transaction sender/class declarer. The main open question, which I could not fully verify within the available tooling, is whether new `DECLARE` transactions in the current gateway still accept Cairo 0 (deprecated) classes, or whether this is only reachable via already-declared legacy classes on chain; this affects whether an attacker can freshly declare such a malformed class or must find/target a pre-existing one.

### Recommendation
Change `CompiledClassV0::constructor_selector` to use `.get(&EntryPointType::Constructor)` instead of index syntax, so a missing key returns `None` (interpreted as "no constructor") rather than panicking:
```rust
fn constructor_selector(&self) -> Option<EntryPointSelector> {
    self.entry_points_by_type.get(&EntryPointType::Constructor)?.first().map(|ep| ep.selector)
}
```
Additionally, audit other `HashMap`/`Vec` index accesses on user-controlled, declared contract class data (both Cairo 0 `entry_points_by_type` and any similar structures) for the same unchecked-indexing pattern.

### Proof of Concept
1. Construct (or locate) a Cairo 0 contract class JSON whose `entry_points_by_type` object omits the `"CONSTRUCTOR"` key (only `"EXTERNAL"`/`"L1_HANDLER"` present), rather than including `"CONSTRUCTOR": []`.
2. Declare this class (if the gateway still accepts Cairo 0 declares) or reference an already-declared class hash on chain that has this shape.
3. Submit a `DEPLOY_ACCOUNT` transaction (or have a contract invoke the `deploy` syscall) targeting this class hash.
4. During execution, `execute_constructor_entry_point` calls `compiled_class.constructor_selector()`, which indexes the map with `[&EntryPointType::Constructor]` and panics with `"no entry found for key"`, aborting execution of that call/transaction on the node instead of returning a normal "no constructor" `Ok(None)` result.

**Note on uncertainty:** I was not able to confirm, within the tools available, whether current gateway validation still permits declaring new Cairo 0 (deprecated) classes, or whether the vulnerable path can only be reached via legacy classes declared before such restrictions were introduced. This affects the precise attacker workflow but not the existence of the unchecked-indexing bug itself in `constructor_selector`.

### Citations

**File:** crates/blockifier/src/execution/contract_class.rs (L342-345)
```rust
impl CompiledClassV0 {
    fn constructor_selector(&self) -> Option<EntryPointSelector> {
        Some(self.entry_points_by_type[&EntryPointType::Constructor].first()?.selector)
    }
```

**File:** crates/starknet_api/src/deprecated_contract_class.rs (L16-27)
```rust
/// A deprecated contract class.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct ContractClass {
    // Starknet does not verify the abi. If we can't parse it, we set it to None.
    #[serde(default, deserialize_with = "deserialize_optional_contract_class_abi_entry_vector")]
    pub abi: Option<Vec<ContractClassAbiEntry>>,
    pub program: Program,
    /// The selector of each entry point is a unique identifier in the program.
    // TODO(Yair): Consider changing to IndexMap, since this is used for computing the
    // class hash.
    pub entry_points_by_type: HashMap<EntryPointType, Vec<EntryPointV0>>,
}
```

**File:** crates/blockifier/src/execution/entry_point.rs (L573-600)
```rust
pub fn execute_constructor_entry_point(
    state: &mut dyn State,
    context: &mut EntryPointExecutionContext,
    ctor_context: ConstructorContext,
    calldata: Calldata,
    remaining_gas: &mut u64,
) -> ConstructorEntryPointExecutionResult<CallInfo> {
    let strip_vm_frames = context.versioned_constants().strip_vm_frames_in_sierra_gas;
    // Ensure the class is declared (by reading it).
    let compiled_class = state.get_compiled_class(ctor_context.class_hash).map_err(|error| {
        ConstructorEntryPointExecutionError::new(
            EntryPointExecutionError::from(error)
                .annotated(TrackedResource::CairoSteps, strip_vm_frames),
            &ctor_context,
            None,
        )
    })?;
    let Some(constructor_selector) = compiled_class.constructor_selector() else {
        // Contract has no constructor.
        return handle_empty_constructor(
            compiled_class,
            context,
            &ctor_context,
            calldata,
            *remaining_gas,
        )
        .map_err(|error| ConstructorEntryPointExecutionError::new(error, &ctor_context, None));
    };
```
