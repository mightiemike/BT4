### Title
Unsafe `audited_libfuncs_only: false` Sierra compilation configuration lets a Declare transaction sender register unaudited libfuncs, breaking Cairo VM security assumptions - (File: crates/apollo_compile_to_casm/src/compiler.rs)

### Summary
`SierraCompilationConfig.audited_libfuncs_only` controls whether the Sierra→CASM compiler restricts a declared class's libfuncs to the audited allow-list or accepts *any* libfunc ("all"). This directly parallels the Cassandra CVE-2021-44521 pattern: a documented-as-unsafe configuration flag (`enable_scripted_user_defined_functions`) that, when toggled, lets a permitted-but-unprivileged actor (there: a UDF creator; here: any account that sends a Declare transaction) push code that runs outside the audited/sandboxed execution assumptions of the system. In this repository the flag is actually deployed as `false` in a production overlay (`deployments/sequencer/configs/overlays/hybrid/sepolia-integration/services/sierra-compiler.yaml`), while `mainnet`/`sepolia-alpha` keep it `true`.

### Finding Description
The compiler builds its libfunc-list argument directly from the config: [1](#0-0) 

`audited_libfuncs_only` defaults to `true`, but is a plain boolean knob: [2](#0-1) 

and it is explicitly documented as the safety boundary between "audited" and "all" libfuncs, with the test suite acknowledging some libfuncs used by "all" are *not yet audited*: [3](#0-2) 

The audited allow-list exists specifically to keep declared Sierra programs restricted to libfuncs whose lowering to CASM has been reviewed for VM safety (the same VM whose security invariants are exercised by tests such as `test_vm_execution_security_failures`, covering out-of-bounds/segment escape/relocatable corruption classes of bugs): [4](#0-3) 

When `audited_libfuncs_only` is `false`, the gateway's Sierra compiler will compile and accept a Declare transaction using *any* libfunc, including ones not yet vetted against these VM-safety invariants (e.g. the `PENDING_LIBFUNCS` set already called out as "not yet in Cairo's audited list"). This is exactly the Cassandra pattern: the org sets an "unsafe" config value in one environment, and any account with ordinary Declare permission (no special privilege required — Declare is a standard, unprivileged transaction type) can then submit a class exploiting the un-vetted libfunc surface to violate execution-time safety assumptions of the CASM VM (memory/segment corruption class of bugs), rather than being confined to well-understood, audited operations.

The repository itself demonstrates this is a live, reachable configuration, not just a theoretical toggle — the sepolia-integration deployment overlay sets it to `false`: [5](#0-4) 
while mainnet and sepolia-alpha pin it to `true`: [6](#0-5) 

### Impact Explanation
If an operator (or an environment inheriting misconfigured overlays) runs with `audited_libfuncs_only: false`, any account can submit a Declare transaction containing libfuncs that have not been vetted for CASM-level VM safety. Because these libfuncs are, by definition, outside the audited safety review, they can violate memory/segment invariants the VM's security model relies on (the same class of invariant covered by `test_vm_execution_security_failures`), potentially enabling out-of-bounds memory manipulation during subsequent execution/re-execution of the declared class. This can lead to non-deterministic or diverging execution between nodes running with different libfunc restrictions, or to execution results that violate the VM's safety guarantees — both of which threaten state-commitment correctness and honest-node consensus on committed roots.

### Likelihood Explanation
Likelihood depends entirely on operator configuration: the attack requires no special privilege beyond submitting an ordinary Declare transaction, but it is only exploitable when `audited_libfuncs_only` is set to `false`. This repository shows that value is not merely a theoretical footgun — it is actually configured `false` in the sepolia-integration environment shipped in this repo, confirming the "unsafe configuration" is reachable in practice, mirroring the Cassandra advisory's framing of a documented-but-still-dangerous setting.

### Recommendation
- Treat `audited_libfuncs_only: false` as an explicitly unsafe/test-only mode, never permitted on any network that processes real value (mirroring the Cassandra advisory's continued treatment of the UDF-scripting configuration as unsafe).
- Add a startup/config-validation guard that refuses to start a Declare-accepting gateway with `audited_libfuncs_only: false` unless an explicit "insecure/test network" flag is also set, and fail closed by default.
- Ensure `PENDING_LIBFUNCS` and any non-audited libfuncs reachable via `all` are inventoried and either fully security-reviewed or excluded regardless of the flag, so that "all" cannot silently include unreviewed opcodes with un-analyzed VM security properties.

### Proof of Concept
1. Deploy/point a sequencer at the `sepolia-integration` overlay (or otherwise set `sierra_compiler_config.audited_libfuncs_only: false`), as done in `deployments/sequencer/configs/overlays/hybrid/sepolia-integration/services/sierra-compiler.yaml`.
2. As an ordinary, unprivileged account, submit a Declare transaction whose Sierra program uses a libfunc present in the "all" list but absent from `BUILTIN_AUDITED_LIBFUNCS_LIST` (e.g. one of the `PENDING_LIBFUNCS` entries, or any future unreviewed libfunc added to `allowed_libfuncs.json`).
3. The gateway's `SierraToCasmCompiler::compile` accepts and compiles the class (`--allowed-libfuncs-list-name all`), producing CASM that a node running the audited-only policy would have rejected.
4. Execute or re-execute a transaction invoking this class; if the unreviewed libfunc's CASM lowering violates a VM-safety invariant (of the kind asserted by `test_vm_execution_security_failures`), this diverges execution behavior/results between nodes with differing `audited_libfuncs_only` settings, or corrupts VM memory/segment state during execution.

### Citations

**File:** crates/apollo_compile_to_casm/src/compiler.rs (L35-41)
```rust
        let additional_args = &[
            "--add-pythonic-hints",
            "--max-bytecode-size",
            &self.config.max_bytecode_size.to_string(),
            "--allowed-libfuncs-list-name",
            if self.config.audited_libfuncs_only { "audited" } else { "all" },
        ];
```

**File:** crates/apollo_sierra_compilation_config/src/config.rs (L9-24)
```rust
pub const DEFAULT_MAX_BYTECODE_SIZE: usize = 80 * 1024;
pub const DEFAULT_MAX_MEMORY_USAGE: u64 = 5 * 1024 * 1024 * 1024;
pub const DEFAULT_MAX_CPU_TIME: u64 = 60;
pub const DEFAULT_AUDITED_LIBFUNCS_ONLY: bool = true;

#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct SierraCompilationConfig {
    /// CASM bytecode size limit (in felts).
    pub max_bytecode_size: usize,
    /// Compilation process’s virtual memory (address space) byte limit.
    pub max_memory_usage: u64,
    /// Compilation process's CPU time limit (in seconds).
    pub max_cpu_time: u64,
    /// If true, compile with audited libfuncs only; if false, allow all libfuncs.
    pub audited_libfuncs_only: bool,
}
```

**File:** crates/apollo_compile_to_casm/src/compile_test.rs (L37-40)
```rust
// Libfuncs in allowed_libfuncs.json but not yet in Cairo's audited list.
// Remove entries once they're added to the audited list.
const PENDING_LIBFUNCS: &[&str] =
    &["sha512_process_block_syscall", "sha512_state_handle_digest", "sha512_state_handle_init"];
```

**File:** crates/blockifier/src/execution/entry_point_test.rs (L234-316)
```rust
#[test]
fn test_vm_execution_security_failures() {
    let chain_info = ChainInfo::create_for_testing();
    let security_contract = FeatureContract::SecurityTests;
    let state = &mut test_state(&chain_info, BALANCE, &[(security_contract, 1)]);

    run_security_test(
        state,
        security_contract,
        "Expected relocatable",
        "test_nonrelocatable_syscall_ptr",
        calldata![],
    );
    run_security_test(
        state,
        security_contract,
        "Unknown value for memory cell",
        "test_unknown_memory",
        calldata![],
    );
    run_security_test(
        state,
        security_contract,
        "can't subtract two relocatable values with different segment indexes",
        "test_subtraction_between_relocatables",
        calldata![],
    );
    run_security_test(
        state,
        security_contract,
        "can't add two relocatable values",
        "test_relocatables_addition_failure",
        calldata![],
    );
    run_security_test(
        state,
        security_contract,
        "op0 must be known in double dereference",
        "test_op0_unknown_double_dereference",
        calldata![],
    );
    run_security_test(
        state,
        security_contract,
        "Out of bounds access to program segment",
        "test_write_to_program_segment",
        calldata![],
    );
    run_security_test(
        state,
        security_contract,
        "Cannot exit main scope.",
        "test_exit_main_scope",
        calldata![],
    );
    run_security_test(
        state,
        security_contract,
        "Every enter_scope() requires a corresponding exit_scope()",
        "test_missing_exit_scope",
        calldata![],
    );
    run_security_test(
        state,
        security_contract,
        "maximum offset value exceeded",
        "test_out_of_bound_memory_value",
        calldata![],
    );
    run_security_test(
        state,
        security_contract,
        "Memory addresses must be relocatable",
        "test_non_relocatable_memory_address",
        calldata![],
    );
    run_security_test(
        state,
        security_contract,
        "Bad expr: {test}. (Cannot evaluate ap-based or complex references: ['test'])",
        "test_bad_expr_eval",
        calldata![],
    );
```

**File:** deployments/sequencer/configs/overlays/hybrid/sepolia-integration/services/sierra-compiler.yaml (L1-5)
```yaml
include: configs/overlays/hybrid/common/services/sierra-compiler.yaml

config:
  sequencerConfig:
    sierra_compiler_config.audited_libfuncs_only: false
```

**File:** deployments/sequencer/configs/overlays/hybrid/mainnet/services/sierra-compiler.yaml (L1-5)
```yaml
include: configs/overlays/hybrid/common/services/sierra-compiler.yaml

config:
  sequencerConfig:
    sierra_compiler_config.audited_libfuncs_only: true
```
