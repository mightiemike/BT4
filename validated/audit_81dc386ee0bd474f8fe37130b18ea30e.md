This confirms the finding: the validator binary explicitly sets a panic hook via `solana_metrics::set_panic_hook`/`std::panic::set_hook` (in `metrics/src/metrics.rs`) that calls `std::process::exit(1)` on **any** panic in **any** thread. This is a real, in-production mechanism (not test-only) — meaning any uncaught Rust panic during transaction/instruction processing terminates the whole validator process, and since builtin-program execution is deterministic across the cluster, an attacker-triggerable panic in a builtin would simultaneously crash all validators, i.e. a cluster halt.

### Title
Uncaught Rust panics in builtin/native program execution crash the validator process, no catch_unwind analog to Soroban's try_call fix - ([File: program-runtime/src/invoke_context.rs])

### Summary
The Soroban patch added `catch_unwind` around native-contract calls in `try_call` so a panicking native contract could not abort the whole host process (stellar/rs-soroban-env#548, fixing issue #430). Agave has the analogous gap: the production path that invokes builtin programs, `InvokeContext::process_executable_chain`, calls `vm.invoke_function(function)` directly with no panic isolation, and the validator installs a global panic hook that force-exits the process on any thread panic.

### Finding Description
`process_executable_chain` in [1](#0-0)  invokes the builtin function via `EbpfVm::invoke_function`, which runs the builtin's Rust `declare_builtin_function!`/`declare_process_instruction!` closure directly on the calling thread (see the macro expansion at [2](#0-1) ). There is no `std::panic::catch_unwind` anywhere in this call chain in production code — the only `catch_unwind` around a builtin-style entrypoint exists in the test-only `solana-program-test` crate's `invoke_builtin_function` (`#![cfg(feature = "agave-unstable-api")]`), as shown in [3](#0-2)  and exercised by the dedicated test `panic_test` in [4](#0-3) . That protection does not exist for real builtin programs (System, Vote, Stake, Config, Compute Budget, Address Lookup Table, BPF Loader family) executed by `process_executable_chain` in the real runtime.

Separately, the validator installs a process-wide panic hook that force-exits the process on any panic from any thread: `solana_metrics::set_panic_hook` in [5](#0-4)  calls `std::process::exit(1)` after any panic, and it is wired up unconditionally in `execute()` at [6](#0-5) . This means any Rust panic encountered while processing a transaction (e.g., an `unwrap()`, array-index, or arithmetic-overflow panic inside a builtin program's instruction-processing code) is not merely a local thread failure — it terminates the entire validator process.

### Impact Explanation
Because builtin program execution is fully deterministic given the same ledger state and transaction, any panic-inducing edge case reachable by an ordinary client transaction into a builtin program (System/Vote/Stake/Config/ALT/loader programs) would be hit identically by every validator processing that block. Combined with the process-exit-on-panic hook, this is a cluster-halting condition: a single malformed-but-otherwise-sanitized transaction that triggers an unguarded panic path inside a builtin's instruction handler could simultaneously crash the entire validator set, rather than being safely converted into an `InstructionError` for that one transaction.

### Likelihood Explanation
Likelihood depends on whether any reachable panic path currently exists inside the builtin programs' instruction-processing code (this specific report doesn't identify one, only the missing structural safety net). Historically, Solana/Agave builtin-program bugs of this exact class (an `unwrap`/overflow panic reachable from user-controlled instruction data) have caused real mainnet halts, which is why this bug class is treated as high severity even absent a currently known concrete trigger. The missing `catch_unwind` isolation in `process_executable_chain` means the runtime has zero defense-in-depth against such a panic once introduced or discovered in any builtin.

### Recommendation
Wrap the builtin invocation in `process_executable_chain` (and any other place that runs builtin `declare_builtin_function!` closures on the hot path, e.g. `native_invoke_signed`) with `std::panic::catch_unwind(AssertUnwindSafe(...))`, converting a caught panic into `InstructionError::ProgramFailedToComplete` (mirroring the pattern already used in `solana-program-test`'s `invoke_builtin_function`), so that a panic inside a builtin program's Rust code fails only the offending instruction/transaction instead of crashing the process. Additionally/alternatively, audit whether the global panic-hook process-exit behavior should be scoped away from transaction-processing threads.

### Proof of Concept
No concrete panic-triggering builtin instruction was identified in this codebase scan; the finding is the absence of the panic-isolation control (`catch_unwind`) around `EbpfVm::invoke_function`/builtin dispatch in `process_executable_chain`, contrasted with its presence in the test-only `solana-program-test` equivalent (`program-test/src/lib.rs:invoke_builtin_function`) and confirmed by that crate's dedicated `panic_test` demonstrating the exact scenario (a builtin that panics) that production code does not defend against.

### Citations

**File:** program-runtime/src/invoke_context.rs (L67-98)
```rust
macro_rules! declare_process_instruction {
    ($process_instruction:ident, $cu_to_consume:expr, |$invoke_context:ident| $inner:tt) => {
        $crate::solana_sbpf::declare_builtin_function!(
            $process_instruction,
            fn rust(
                invoke_context: &mut $crate::invoke_context::InvokeContext<'_, '_>,
                _arg0: u64,
                _arg1: u64,
                _arg2: u64,
                _arg3: u64,
                _arg4: u64,
            ) -> Result<u64, Box<dyn std::error::Error>> {
                fn process_instruction_inner(
                    $invoke_context: &mut $crate::invoke_context::InvokeContext,
                ) -> std::result::Result<(), $crate::__private::InstructionError>
                    $inner

                let consumption_result = if $cu_to_consume > 0
                {
                    invoke_context.compute_meter.consume_checked($cu_to_consume)
                } else {
                    Ok(())
                };
                consumption_result
                    .and_then(|_| {
                        process_instruction_inner(invoke_context)
                            .map(|_| 0)
                            .map_err(|err| Box::new(err) as Box<dyn std::error::Error>)
                    })
                    .into()
            }
        );
```

**File:** program-runtime/src/invoke_context.rs (L690-702)
```rust
        let mut vm = EbpfVm::new(
            Arc::clone(
                &**self
                    .environment_config
                    .program_runtime_environments
                    .get_env_for_execution(),
            ),
            SBPFVersion::V0,
            // Removes lifetime tracking
            unsafe { std::mem::transmute::<&mut InvokeContext, &mut InvokeContext>(self) },
            0,
        );
        vm.invoke_function(function);
```

**File:** program-test/src/lib.rs (L156-174)
```rust
    // Execute the program
    match std::panic::catch_unwind(AssertUnwindSafe(|| {
        builtin_function(program_id, &account_infos, input)
    })) {
        Ok(program_result) => {
            program_result.map_err(|program_error| {
                let err = InstructionError::from(u64::from(program_error));
                stable_log::program_failure(&log_collector, program_id, &err);
                let err: Box<dyn std::error::Error> = Box::new(err);
                err
            })?;
        }
        Err(_panic_error) => {
            let err = InstructionError::ProgramFailedToComplete;
            stable_log::program_failure(&log_collector, program_id, &err);
            let err: Box<dyn std::error::Error> = Box::new(err);
            Err(err)?;
        }
    };
```

**File:** program-test/tests/panic.rs (L12-40)
```rust
fn panic(_program_id: &Pubkey, _accounts: &[AccountInfo], _input: &[u8]) -> ProgramResult {
    panic!("I panicked");
}

#[tokio::test]
async fn panic_test() {
    let program_id = Pubkey::new_unique();

    let program_test = ProgramTest::new("panic", program_id, processor!(panic));

    let context = program_test.start_with_context().await;

    let instruction = Instruction::new_with_bytes(program_id, &[], vec![]);

    let transaction = Transaction::new_signed_with_payer(
        &[instruction],
        Some(&context.payer.pubkey()),
        &[&context.payer],
        context.last_blockhash,
    );
    assert_eq!(
        context
            .banks_client
            .process_transaction(transaction)
            .await
            .unwrap_err()
            .unwrap(),
        TransactionError::InstructionError(0, InstructionError::ProgramFailedToComplete)
    );
```

**File:** metrics/src/metrics.rs (L531-544)
```rust
/// Hook the panic handler to generate a data point on each panic
pub fn set_panic_hook(program: &'static str, version: Option<String>) {
    static SET_HOOK: Once = Once::new();
    SET_HOOK.call_once(|| {
        let default_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |ono| {
            default_hook(ono);
            submit_panic_datapoint(program, &version, ono);

            // Exit cleanly so the process don't limp along in a half-dead state
            std::process::exit(1);
        }));
    });
}
```

**File:** validator/src/commands/run/execute.rs (L155-156)
```rust
    solana_metrics::set_host_id(identity_keypair.pubkey().to_string());
    solana_metrics::set_panic_hook("validator", Some(String::from(solana_version)));
```
