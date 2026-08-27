Based on the code, the claimed vulnerability does not hold up.

`ComputeBudgetLimits::default()` sets `updated_heap_bytes: MIN_HEAP_FRAME_BYTES` and `compute_unit_limit: MAX_COMPUTE_UNIT_LIMIT` [1](#0-0) , and `get_compute_budget_and_limits` directly copies `self.updated_heap_bytes` into `SVMTransactionExecutionBudget::heap_size` and `self.compute_unit_limit` into `compute_unit_limit`, with all other VM parameters (stack frame size, call depth, instruction stack depth, trace length) sourced unconditionally from `SVMTransactionExecutionBudget::new_with_defaults` — none of which are influenced by the attacker-supplied heap/CU values [2](#0-1) [3](#0-2) .

Stack depth (`max_call_depth = MAX_CALL_DEPTH = 64`) and `stack_frame_size` are compile-time/fixed constants from `solana_sbpf::vm::get_stack_frame_size()`, entirely independent of heap size, compute unit limit, or compute unit price — the attacker has no lever over them at all [4](#0-3) . Heap size is bounded and sanitized to `[MIN_HEAP_FRAME_BYTES, MAX_HEAP_FRAME_BYTES]` and must be a multiple of 1024 in `sanitize_and_convert_to_compute_budget_limits`, with any invalid value rejected before execution and thus before it ever reaches `default()`/`get_compute_budget_and_limits` [5](#0-4) [6](#0-5) .

There is no code path by which setting compute unit price to 1 micro-lamport with the smallest CU limit produces a heap or stack allocation larger than what is returned in `SVMTransactionExecutionBudget` — the returned struct's `heap_size` field is exactly the value used to configure the VM (`heap_size` is passed through as-is, and callers use this same struct to build the VM), and stack/call-depth fields are static constants that are always reflected identically regardless of any budget instruction. The premise that `default` (or the derived limits) can diverge from the actual VM configuration is not supported by the code.

### No Vulnerability found for this question.

### Citations

**File:** compute-budget/src/compute_budget_limits.rs (L27-36)
```rust
impl Default for ComputeBudgetLimits {
    fn default() -> Self {
        ComputeBudgetLimits {
            updated_heap_bytes: MIN_HEAP_FRAME_BYTES,
            compute_unit_limit: MAX_COMPUTE_UNIT_LIMIT,
            compute_unit_price: 0,
            loaded_accounts_bytes: MAX_LOADED_ACCOUNTS_DATA_SIZE_BYTES,
        }
    }
}
```

**File:** compute-budget/src/compute_budget_limits.rs (L44-54)
```rust
    ) -> SVMTransactionExecutionAndFeeBudgetLimits {
        SVMTransactionExecutionAndFeeBudgetLimits {
            budget: SVMTransactionExecutionBudget {
                compute_unit_limit: u64::from(self.compute_unit_limit),
                heap_size: self.updated_heap_bytes,
                ..SVMTransactionExecutionBudget::new_with_defaults(simd_0268_active)
            },
            loaded_accounts_data_size_limit: loaded_accounts_data_size_limit.get(),
            fee_details,
        }
    }
```

**File:** program-runtime/src/execution_budget.rs (L24-41)
```rust
pub const MAX_CALL_DEPTH: usize = 64;

pub const MAX_COMPUTE_UNIT_LIMIT: u32 = 1_400_000;

/// Roughly 0.5us/page, where page is 32K; given roughly 15CU/us, the
/// default heap page cost = 0.5 * 15 ~= 8CU/page
pub const DEFAULT_HEAP_COST: u64 = 8;
pub const DEFAULT_INSTRUCTION_COMPUTE_UNIT_LIMIT: u32 = 200_000;
// SIMD-170 defines max CUs to be allocated for any builtin program instructions, that
// have not been migrated to sBPF programs.
pub const MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT: u32 = 3_000;
pub const MAX_HEAP_FRAME_BYTES: u32 = 256 * 1024;
pub const MIN_HEAP_FRAME_BYTES: u32 = HEAP_LENGTH as u32;

/// The total accounts data a transaction can load is limited to 64MiB to not break
/// anyone in Mainnet-beta today. It can be set by set_loaded_accounts_data_size_limit instruction
pub const MAX_LOADED_ACCOUNTS_DATA_SIZE_BYTES: NonZeroU32 =
    NonZeroU32::new(64 * 1024 * 1024).unwrap();
```

**File:** program-runtime/src/execution_budget.rs (L73-85)
```rust
impl SVMTransactionExecutionBudget {
    pub fn new_with_defaults(simd_0268_active: bool) -> Self {
        SVMTransactionExecutionBudget {
            compute_unit_limit: u64::from(MAX_COMPUTE_UNIT_LIMIT),
            max_instruction_stack_depth: get_max_instruction_stack_depth(simd_0268_active),
            max_instruction_trace_length: MAX_INSTRUCTION_TRACE_LENGTH,
            sha256_max_slices: 20_000,
            max_call_depth: MAX_CALL_DEPTH,
            stack_frame_size: solana_sbpf::vm::get_stack_frame_size(),
            heap_size: u32::try_from(solana_program_entrypoint::HEAP_LENGTH).unwrap(),
        }
    }
}
```

**File:** compute-budget-instruction/src/compute_budget_instruction_details.rs (L105-119)
```rust
        // Sanitize requested heap size
        let updated_heap_bytes =
            if let Some((index, requested_heap_size)) = self.requested_heap_size {
                if Self::sanitize_requested_heap_size(requested_heap_size) {
                    requested_heap_size
                } else {
                    return Err(TransactionError::InstructionError(
                        index,
                        InstructionError::InvalidInstructionData,
                    ));
                }
            } else {
                MIN_HEAP_FRAME_BYTES
            }
            .min(MAX_HEAP_FRAME_BYTES);
```

**File:** compute-budget-instruction/src/compute_budget_instruction_details.rs (L192-194)
```rust
    fn sanitize_requested_heap_size(bytes: u32) -> bool {
        (MIN_HEAP_FRAME_BYTES..=MAX_HEAP_FRAME_BYTES).contains(&bytes) && bytes.is_multiple_of(1024)
    }
```
