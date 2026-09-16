### Title
Panic (Denial-of-Service) via Unrecognized Builtin String in Declared Sierra Contract Class - (File: crates/blockifier/src/execution/contract_class.rs)

### Summary
The reported CVE describes a NULL pointer dereference in HDF5 caused by an unvalidated "type" field in a variable-length datatype, triggered while reading an attacker-crafted attribute. The analogous bug class here is: a field within a class artifact that is trusted to always be one of a fixed known set of values (a "type tag") is converted via an infallible-looking API that actually panics on invalid input, and this conversion is reachable via processing a class supplied by an unprivileged actor.

### Finding Description
When a `ContractClass::V1` (CASM class, i.e. `VersionedCasm = (CasmContractClass, SierraVersion)`) is converted into the executable `CompiledClassV1` representation, the entry points' builtin name strings are parsed via `BuiltinName::from_str(builtin)` and the `Result` is unwrapped with `.expect("Unrecognized builtin.")`: [1](#0-0) 

This function, `convert_entry_points_v1`, is called from the `From<&CasmContractEntryPoints> for EntryPointsByType<EntryPointV1>` impl, which is in turn invoked directly inside `TryFrom<VersionedCasm> for CompiledClassV1::try_from`: [2](#0-1) 

`CasmContractEntryPoint.builtins` is a `Vec<String>` field originating from the `cairo_lang_starknet_classes::casm_contract_class::CasmContractClass` structure — an arbitrary, attacker-influenceable string list once a `ContractClass::V1` value is constructed from any externally supplied or previously-stored CASM JSON (e.g., re-execution/replay paths, state-reader paths building `ContractClass` from stored/fetched CASM, or any component that deserializes a `CasmContractClass` and feeds it into `TryFrom<VersionedCasm>` without first validating that every `builtins` string is one of the known `BuiltinName` variants). Unlike the well-guarded declare-transaction validation path in the gateway (`validate_sierra_version`, `validate_class_length`, etc., in `crates/apollo_gateway/src/stateless_transaction_validator.rs`), there is no validation step ensuring the builtin strings recorded in a CASM entry point are members of the known builtin set before this conversion executes. If any string fails `BuiltinName::from_str`, the `.expect(...)` triggers a Rust panic instead of returning a typed error such as `PreExecutionError::InvalidBuiltin`, which is the pattern used elsewhere for exactly this class of problem (e.g., `validate_entry_point_builtins` and `PreExecutionError::InvalidBuiltin`): [3](#0-2) [4](#0-3) 

The unbounded `.expect()` in `convert_entry_points_v1` is the exact analog of the "invalid variable-length datatype type tag → NULL dereference" bug class: an untyped/unchecked tag-like field (`builtins: Vec<String>`) is trusted to be well-formed, and consuming it panics/crashes the process rather than surfacing a recoverable error.

### Impact Explanation
A panic in this conversion path aborts (or, depending on panic-handling configuration, poisons/kills) the executing thread/process of the sequencer or full node performing the conversion. Because `TryFrom<VersionedCasm> for CompiledClassV1` sits on the class-materialization path that any node must exercise before it can execute or re-execute a Cairo1 class, a reachable crash here can cause honest-node divergence or an inability of affected nodes to process blocks/transactions referencing the malformed class — consistent with the required "network unable to confirm new transactions" or crash-based impact criteria.

### Likelihood Explanation
I was not able to fully confirm, within the available tool budget, whether the primary declare-transaction ingestion path (gateway → `apollo_compile_to_casm` Sierra-to-CASM compiler) can ever produce a `CasmContractEntryPoint.builtins` string that is not a valid `BuiltinName`, since that compiler is a trusted component using the audited `cairo-lang` toolchain and would typically emit only well-formed builtin names. The higher-likelihood reachable path is any component that deserializes/loads a `CasmContractClass`/`ContractClass::V1` from an external or previously-persisted source (e.g. re-execution utilities, state readers, feeder-gateway/full-node RPC responses) without re-validating builtin names before calling `TryFrom<VersionedCasm>`. Because I could not fully trace every call site (`crates/apollo_rpc_execution/src/state_reader.rs`, `crates/apollo_state_reader/src/apollo_state.rs`, `crates/blockifier_reexecution/src/compile.rs`, `crates/native_blockifier/src/state_readers/py_state_reader.rs`, etc. all reference this conversion) to determine which are reachable from a strictly unprivileged transaction/declare/L1-message sender versus only from operator/RPC-provided inputs, this must be flagged as **uncertain**: it is plausible this is only reachable from trusted/internal recompilation, not from a raw attacker-controlled declare payload, which would reduce it out of scope per the "no impact" / "no malicious node" exclusion rules.

### Recommendation
Regardless of exact reachability, replace the panicking `.expect("Unrecognized builtin.")` in `convert_entry_points_v1` with a fallible conversion that propagates a typed error (mirroring `PreExecutionError::InvalidBuiltin` or a new `ProgramError`/`TryFrom` failure), so that `TryFrom<VersionedCasm> for CompiledClassV1` returns `Err(..)` instead of panicking whenever a builtin string does not correspond to a known `BuiltinName`. This closes the gap regardless of which call site actually supplies attacker-influenced CASM.

### Proof of Concept
Conceptual PoC (illustrative, not necessarily achievable through the standard declare-transaction gateway path since that relies on the trusted Sierra-to-CASM compiler):
```rust
let malicious_entry_point = CasmContractEntryPoint {
    selector: BigUint::from(1u8),
    offset: 0,
    builtins: vec!["not_a_real_builtin".to_string()], // invalid builtin tag
};
let casm = CasmContractClass { entry_points_by_type: CasmContractEntryPoints {
    external: vec![malicious_entry_point], constructor: vec![], l1_handler: vec![]
}, /* ...other required fields... */ };
let versioned_casm: VersionedCasm = (casm, SierraVersion::LATEST);
let _ = CompiledClassV1::try_from(versioned_casm); // panics with "Unrecognized builtin."
``` [1](#0-0) 

**Note on confidence**: Given the remaining uncertainty about whether an unprivileged transaction sender/declarer can drive a `CasmContractEntryPoint.builtins` string with an invalid value all the way into `TryFrom<VersionedCasm>` (versus this being reachable only through internal/trusted recompilation or operator-provided RPC data), this finding should be treated as a **defense-in-depth panic hardening issue** rather than a confirmed, directly-exploitable Medium/High severity analog. A full Devin session with terminal/build access would be needed to trace every call site of `TryFrom<VersionedCasm>` and the `apollo_compile_to_casm` output guarantees to conclusively confirm or reject reachability from a single submitted declare transaction.

### Citations

**File:** crates/blockifier/src/execution/contract_class.rs (L626-678)
```rust
impl TryFrom<VersionedCasm> for CompiledClassV1 {
    type Error = ProgramError;

    fn try_from((class, sierra_version): VersionedCasm) -> Result<Self, Self::Error> {
        let data: Vec<MaybeRelocatable> =
            class.bytecode.iter().map(|x| MaybeRelocatable::from(Felt::from(&x.value))).collect();

        let mut hints: HashMap<usize, Vec<HintParams>> = HashMap::new();
        for (i, hint_list) in class.hints.iter() {
            let hint_params: Result<Vec<HintParams>, ProgramError> =
                hint_list.iter().map(hint_to_hint_params).collect();
            hints.insert(*i, hint_params?);
        }

        // Collect a sting to hint map so that the hint processor can fetch the correct [Hint]
        // for each instruction.
        let mut string_to_hint: HashMap<String, Hint> = HashMap::new();
        for (_, hint_list) in class.hints.iter() {
            for hint in hint_list.iter() {
                string_to_hint.insert(serde_json::to_string(hint)?, hint.clone());
            }
        }

        let builtins = vec![]; // The builtins are initialize later.
        let main = Some(0);
        let reference_manager = ReferenceManager { references: Vec::new() };
        let identifiers = HashMap::new();
        let error_message_attributes = vec![];
        let instruction_locations = None;

        let program = Program::new(
            builtins,
            data,
            main,
            hints,
            reference_manager,
            identifiers,
            error_message_attributes,
            instruction_locations,
        )?;

        let bytecode_segment_felt_sizes =
            NestedFeltCounts::new(&class.get_bytecode_segment_lengths(), &class.bytecode);

        Ok(CompiledClassV1(Arc::new(ContractClassV1Inner {
            program,
            entry_points_by_type: (&class.entry_points_by_type).into(),
            hints: string_to_hint,
            sierra_version,
            bytecode_segment_felt_sizes,
        })))
    }
}
```

**File:** crates/blockifier/src/execution/contract_class.rs (L728-741)
```rust
fn convert_entry_points_v1(external: &[CasmContractEntryPoint]) -> Vec<EntryPointV1> {
    external
        .iter()
        .map(|ep| EntryPointV1 {
            selector: EntryPointSelector(Felt::from(&ep.selector)),
            offset: EntryPointOffset(ep.offset),
            builtins: ep
                .builtins
                .iter()
                .map(|builtin| BuiltinName::from_str(builtin).expect("Unrecognized builtin."))
                .collect(),
        })
        .collect()
}
```

**File:** crates/blockifier/src/execution/entry_point_execution.rs (L216-224)
```rust
/// Checks that `builtins` is an ordered subsequence of [`CAIRO1_SUPPORTED_BUILTINS`], rejecting
/// unsupported builtins (e.g. `ecdsa`, `keccak`) and non-canonical ordering.
fn validate_entry_point_builtins(builtins: &[BuiltinName]) -> Result<(), PreExecutionError> {
    if is_subsequence(builtins, &CAIRO1_SUPPORTED_BUILTINS) {
        Ok(())
    } else {
        Err(PreExecutionError::UnsupportedCairo1Builtins(builtins.to_vec()))
    }
}
```

**File:** crates/blockifier/src/execution/errors.rs (L27-61)
```rust
#[derive(Debug, Error)]
pub enum PreExecutionError {
    #[error("Entry point {:#066x} of type {typ:?} is not unique.", .selector.0)]
    DuplicatedEntryPointSelector { selector: EntryPointSelector, typ: EntryPointType },
    #[error("Entry point {0:?} not found in contract.")]
    EntryPointNotFound(EntryPointSelector),
    #[error("Fraud attempt blocked.")]
    FraudAttempt,
    #[error("Invalid builtin {0}.")]
    InvalidBuiltin(BuiltinName),
    #[error("The constructor entry point must be named 'constructor'.")]
    InvalidConstructorEntryPointName,
    #[error(transparent)]
    MathError(#[from] MathError),
    #[error(transparent)]
    MemoryError(#[from] MemoryError),
    #[error("No entry points of type {0:?} found in contract.")]
    NoEntryPointOfTypeFound(EntryPointType),
    #[error(transparent)]
    ProgramError(#[from] cairo_vm::types::errors::program_errors::ProgramError),
    #[error(transparent)]
    RunnerError(Box<RunnerError>),
    #[error(transparent)]
    StateError(#[from] StateError),
    #[error("Requested contract address {:#066x} is not deployed.", .0.key())]
    UninitializedStorageAddress(ContractAddress),
    #[error("Called builtins: {0:?} are unsupported in a Cairo0 contract")]
    UnsupportedCairo0Builtin(HashSet<BuiltinName>),
    #[error("Entry point uses unsupported builtins or an invalid builtin order: {0:?}.")]
    UnsupportedCairo1Builtins(Vec<BuiltinName>),
    #[error(
        "Insufficient entry point initial gas, must be greater than the entry point initial \
         budget."
    )]
    InsufficientEntryPointGas,
```
