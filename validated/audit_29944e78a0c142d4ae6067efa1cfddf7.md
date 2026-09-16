### Title
Sierra-to-CASM/Native compiler subprocess resource limits are a complete no-op on Windows, allowing unbounded CPU/memory consumption from a single Declare transaction - (File: crates/apollo_compilation_utils/src/resource_limits/resource_limits_windows.rs)

### Summary
The sequencer compiles attacker-supplied Sierra contract classes (submitted via `Declare` transactions) by spawning an external compiler binary as a subprocess. To contain this untrusted, CPU/memory-intensive external process, the code sets `rlimit`-based CPU-time, file-size, and memory limits — but only on Unix. On Windows the entire `ResourceLimits` implementation is a stub that does nothing.

### Finding Description
`SierraToCasmCompiler::compile` (and the analogous Sierra-to-native compiler) constructs a `ResourceLimits` object from `SierraCompilationConfig::max_cpu_time` / `max_memory_usage` and passes it into `compile_with_args`, which calls `resource_limits.apply(&mut command)` before spawning the compiler binary as a child process: [1](#0-0) [2](#0-1) 

The `ResourceLimits` type is compiled conditionally per platform: [3](#0-2) 

On Unix, `apply` installs a `pre_exec` hook that calls `setrlimit` for CPU time, file size and address-space (memory) limits on the child process before it execs the compiler binary: [4](#0-3) 

On Windows, `ResourceLimits::new`, `set`, and `apply` are all no-ops — `apply` simply returns the `command` unmodified, and `set` returns `Ok(())` without doing anything: [5](#0-4) 

This is structurally identical to the n8n bug class described in the report: a security-relevant sandbox/resource restriction is implemented for one OS (macOS in n8n's case, Unix here) and is silently absent on the others (Linux/Windows in n8n's case, Windows here), with the calling code assuming enforcement happened everywhere.

### Impact Explanation
The compiled input originates from an untrusted `Declare` transaction's Sierra contract class, which any unprivileged account can submit through the gateway/class-manager pipeline that invokes this compiler. On a sequencer node built/run on Windows, the compiler subprocess is spawned with zero enforced CPU-time or memory limits, so a maliciously crafted Sierra class designed to make the reference compiler binary loop indefinitely or allocate unbounded memory can hang or exhaust memory on the compiling node with a single transaction. Because Sierra-to-CASM compilation is on the hot path for both Declare transaction validation and class execution/re-execution, this can degrade or freeze the sequencer's transaction-processing pipeline, matching the allowed impact category of a node/network unable to confirm new transactions.

### Likelihood Explanation
Likelihood depends on whether any node in the network is actually deployed on Windows. The code explicitly supports Windows as a first-class target (dedicated `resource_limits_windows.rs` module, `#[cfg(windows)]` gating), implying it is expected to run there, but I could not confirm from the deployment configs I inspected (which target Kubernetes/Linux container overlays) whether production nodes actually run on Windows. If all production deployments are Linux-only, this analog has no practical exploitability despite being a genuine code-level parity gap. I was unable to fully verify production OS targeting within the available tool budget.

### Recommendation
Implement real OS-level resource containment in `resource_limits_windows.rs` (e.g., Windows Job Objects with `JOB_OBJECT_LIMIT_PROCESS_MEMORY` / `JOB_OBJECT_LIMIT_JOB_TIME`, assigning the compiler child process to the job) instead of the current no-op stub, or explicitly refuse to run the compiler subprocess without enforced limits (fail closed) analogous to the n8n fix's `--dangerously-disable-shell-sandbox` opt-out model, rather than silently proceeding unsandboxed.

### Proof of Concept
1. Build/run the sequencer's Sierra-to-CASM compilation service (`apollo_compile_to_casm`/`apollo_compile_to_native`) on a Windows host.
2. Submit a `Declare` transaction (or trigger class compilation via the class manager) with a Sierra contract class engineered so the external `starknet-sierra-compile`/native compiler binary enters an unbounded loop or allocates unbounded memory during compilation.
3. Observe that, unlike on Unix (where `setrlimit` CPU/memory caps would kill the process via `SIGKILL`/`SIGXFSZ`), the Windows-hosted compiler process runs with no CPU-time or memory ceiling, per `ResourceLimits::apply` at [6](#0-5) , potentially hanging or exhausting host memory and blocking further compilation/transaction processing on that node.

### Citations

**File:** crates/apollo_compile_to_casm/src/compiler.rs (L42-53)
```rust
        let resource_limits = ResourceLimits::new(
            Some(self.config.max_cpu_time),
            None,
            Some(self.config.max_memory_usage),
        );

        let stdout = compile_with_args(
            compiler_binary_path,
            contract_class,
            additional_args,
            resource_limits,
        )?;
```

**File:** crates/apollo_compilation_utils/src/compiler_utils.rs (L33-40)
```rust
    let mut command = Command::new(compiler_binary_path.as_os_str());
    command.arg(temp_file_path).args(additional_args);

    // Apply the resource limits to the command.
    resource_limits.apply(&mut command);

    // Run the compile process.
    let compile_output = command.output()?;
```

**File:** crates/apollo_compilation_utils/src/resource_limits.rs (L1-9)
```rust
#[cfg(unix)]
mod resource_limits_unix;
#[cfg(unix)]
pub use resource_limits_unix::ResourceLimits;

#[cfg(windows)]
mod resource_limits_windows;
#[cfg(windows)]
pub use resource_limits_windows::ResourceLimits;
```

**File:** crates/apollo_compilation_utils/src/resource_limits/resource_limits_unix.rs (L78-114)
```rust
    /// Set all defined resource limits for the current process. Limits set to `None` are ignored.
    pub fn set(&self) -> io::Result<()> {
        [self.cpu_time.as_ref(), self.file_size.as_ref(), self.memory_size.as_ref()]
            .iter()
            .flatten()
            .try_for_each(|resource_limit| resource_limit.set())
    }

    /// Apply the resource limits to a given command object. This moves the [`ResourceLimits`]
    /// struct into a closure that is held by the given command. The closure is executed in the
    /// child process spawned by the command, right before it invokes the `exec` system call.
    pub fn apply(self, command: &mut Command) -> &mut Command {
        if self.cpu_time.is_none() && self.file_size.is_none() && self.memory_size.is_none() {
            return command;
        }
        unsafe {
            // The `pre_exec` method runs a given closure after the parent process has been forked
            // but before the child process calls `exec`.
            //
            // This closure runs in the child process after a `fork`, which primarily means that any
            // modifications made to memory on behalf of this closure will **not** be visible to the
            // parent process. This environment is often very constrained. Normal operations--such
            // as using `malloc`, accessing environment variables through [`std::env`] or acquiring
            // a mutex--are not guaranteed to work, because after `fork`, only one thread exists in
            // the child process, while there may be multiple threads in the parent process.
            //
            // This closure is considered safe for the following reasons:
            // 1. The [`ResourceLimits`] struct is fully constructed and moved into the closure.
            // 2. No heap allocations occur in the `set` method.
            // 3. `setrlimit` is an async-signal-safe system call, which means it is safe to invoke
            //   after `fork`. This is established in the POSIX `fork` specification:
            //   > ... the child process may only execute async-signal-safe operations until such
            //    time as one of the `exec` functions is called.
            //   (See <https://pubs.opengroup.org/onlinepubs/9699919799/functions/fork.html>)
            command.pre_exec(move || self.set())
        }
    }
```

**File:** crates/apollo_compilation_utils/src/resource_limits/resource_limits_windows.rs (L1-28)
```rust
use std::io;
use std::process::Command;

/// A struct to hold resource limits for a process.
/// Each limit is optional and can be set to `None` if not needed.
/// NOTE: This is a trivial implementation for compiling on windows.
pub struct ResourceLimits;

impl ResourceLimits {
    pub fn new(
        _cpu_time: Option<u64>,
        _file_size: Option<u64>,
        _memory_size: Option<u64>,
    ) -> ResourceLimits {
        ResourceLimits {}
    }

    pub fn set(&self) -> io::Result<()> {
        Ok(())
    }

    /// Apply the resource limits to a given command object. This moves the [`ResourceLimits`]
    /// struct into a closure that is held by the given command. The closure is executed in the
    /// child process spawned by the command, right before it invokes the `exec` system call.
    pub fn apply(self, command: &mut Command) -> &mut Command {
        command
    }
}
```
