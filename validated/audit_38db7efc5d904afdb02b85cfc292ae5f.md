### Title
Excessive CPU utilization from gas-underpriced `promise_and` merging of large `NotReceipt` joint promises - ([File: runtime/near-vm-runner/src/wasmtime_runner/logic.rs])

### Summary
`promise_and` charges gas based only on `promise_idx_count` (the number of promise indices passed in *this* call), but its actual runtime cost is proportional to the total size of the `Vec<ReceiptIndex>` stored in every `Promise::NotReceipt` referenced by those indices, because each is fully `clone()`d and `extend()`ed into a fresh vector on every call. A contract can build one large joint promise once, then repeatedly reference it in cheap `promise_and` calls, paying gas only for a handful of indices while the host performs `O(L)` copy work each time — the same "cheap-to-trigger, expensive-to-process" asymmetry that made HTTP/2 stream resets in Envoy (CVE-2021-32778) an O(N²) CPU exhaustion vector.

### Finding Description
`promise_and` (`runtime/near-vm-runner/src/wasmtime_runner/logic.rs:2385-2434`) builds its result as follows: [1](#0-0) 

For each `promise_idx` in the caller-supplied array it either pushes one `ReceiptIndex` (if the referenced promise is `Promise::Receipt`) or does `receipt_dependencies.extend(receipt_indices.clone())` when the referenced promise is `Promise::NotReceipt(receipt_indices)` (`logic.rs:2418-2420`). The `Promise` enum definition confirms `NotReceipt` carries a `Vec<ReceiptIndex>` of unbounded (up to the configured limit) size: [2](#0-1) 

Gas accounting for the call only covers the base cost and a per-entry cost tied to `promise_idx_count` — the number of elements in the *input* array read from guest memory — not the size of the vectors being cloned: [3](#0-2) 

The only safeguard is a post-hoc length check against `max_number_input_data_dependencies`, performed inside the loop after each `extend`: [4](#0-3) 

This check only bounds the *size of the newly created* `NotReceipt` vector; it does not bound (or charge for) the cost of the `clone()`/`extend()` work performed to get there. Because `receipt_dependencies` is a `Vec` and the loop is 0-indexed over `promise_idx_count` (which can be as small as 2), a contract can:
1. Build one `Promise::NotReceipt` that already holds close to `max_number_input_data_dependencies` entries (a normal, gas-paid operation, done once).
2. Repeatedly call `promise_and([that_promise_idx])` (a single index — cheapest possible call, `promise_idx_count = 1`), each time paying only `base + promise_and_base + promise_and_per_promise * 1`, while the host does `O(L)` work to clone/extend the large existing vector before creating the new (equally large) `NotReceipt`.

Each such call is charged as if it processed one dependency, but it actually copies up to `L ≈ max_number_input_data_dependencies` `ReceiptIndex` values. `promise_batch_then` (`logic.rs:2504-2545`) has the identical pattern — `receipt_indices.clone()` from an existing `NotReceipt` — again with gas metered by `pay_gas_for_new_receipt`'s dependency-count-based cost rather than the true cost of the clone that already happened by the time that function runs.

Repeating this call many times within the gas budget of a single `FunctionCall` action lets an attacker perform far more `O(L)` copy operations than the gas charged would suggest, inflating wall-clock CPU time for the amount of gas burned. Because gas is supposed to be the universal metric bounding computation per unit of chunk-gas-limit, this breaks the gas/CPU-time invariant that block production and chunk validation rely on to stay within their time budget — the same underlying bug class as the Envoy issue (cheap client-side action, disproportionately expensive server-side processing).

### Impact Explanation
If the number of "cheap" `promise_and`/`promise_batch_then` calls referencing a large existing `NotReceipt` promise that fit inside a transaction's prepaid gas is large enough, the aggregate CPU time spent inside a single `FunctionCall` action can grow far beyond what its gas cost implies. Since chunk production and chunk validation must complete within a fixed wall-clock budget per block height, a chunk producer/validator processing such a transaction could fall behind, and other honest validators executing the same transaction may observe divergent timing behavior; more importantly this represents a transaction-triggered mechanism to consume disproportionate host CPU relative to gas paid, which is the class of bug the report calls "transaction-triggered" DoS/slowdown. This qualifies as reachable from a single submitted transaction / contract call (no privileged access required).

### Likelihood Explanation
Any account can deploy or call an existing contract that invokes `promise_and`/`promise_batch_then` with attacker-controlled promise indices via a `FunctionCall` action; no special permissions, staking, or validator status are required. The exploit only needs to build one large joint promise (bounded by `max_number_input_data_dependencies`, a normal, allowed operation) and then loop calling `promise_and` on it with a minimal input array, which is a straightforward, low-effort contract pattern.

### Recommendation
Charge gas for `promise_and` (and `promise_batch_then`) proportional to the *total number of receipt dependencies actually copied* (i.e., the sum of `receipt_indices.len()` for every `NotReceipt` promise being merged), not merely `promise_idx_count`. Alternatively, avoid cloning the full dependency vector on every merge by using a persistent/shared structure (e.g., reference counting or a union-find/DAG representation) so that repeated merges of the same large joint promise do not require re-copying its full contents, and add a check/charge before doing the `extend()` rather than only after.

### Proof of Concept
Conceptual outline (cannot be executed in this environment, but derivable from the cited code):
1. Deploy a contract that creates `N ≈ max_number_input_data_dependencies` cheap promises (e.g., `promise_create` to trivial receivers) and merges them once via `promise_and(all_N_indices)` to obtain promise index `P` holding `Promise::NotReceipt` with `N` entries — this is a normal, gas-paid, one-time cost.
2. In the same or a follow-up `FunctionCall`, loop calling `promise_and(&[P], 1)` (or `promise_and(&[P, P], 2)`) as many times as the prepaid gas allows for `promise_idx_count = 1` calls. Each iteration pays only `base + promise_and_base + promise_and_per_promise * 1`, yet the host performs a full `O(N)` `clone()`/`extend()` of the `NotReceipt` vector referenced by `P` before hitting the `NumberInputDataDependenciesExceeded` check (since merging `P` with itself immediately exceeds the limit but only *after* the clone/extend has already run).
3. Measure that the number of repetitions achievable within the gas limit, multiplied by `N`, yields total `ReceiptIndex` copy operations far exceeding what `promise_and_per_promise` gas paid would predict — demonstrating the CPU/gas mismatch.

This should be validated with an actual runtime-params-estimator benchmark (`PromiseAndPerPromise`, noted in `runtime/runtime-params-estimator/src/cost.rs:664-669` as "Currently not estimated") comparing wall-clock time of many small `promise_and` calls against a large pre-built `NotReceipt` promise versus the gas charged, to confirm the magnitude of the discrepancy.

### Citations

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L2391-2432)
```rust
    ctx.result_state.gas_counter.pay_base(base)?;
    if ctx.context.is_view() {
        return Err(HostError::ProhibitedInView { method_name: "promise_and".to_string() }.into());
    }
    ctx.result_state.gas_counter.pay_base(promise_and_base)?;
    let memory_len =
        promise_idx_count.checked_mul(size_of::<u64>() as u64).ok_or(HostError::IntegerOverflow)?;
    ctx.result_state.gas_counter.pay_per(promise_and_per_promise, memory_len)?;

    // Read indices as little endian u64.
    let promise_indices =
        read_memory(&mut ctx.result_state.gas_counter, memory, promise_idx_ptr, memory_len)?;
    let promise_indices = stdx::as_chunks_exact::<{ size_of::<u64>() }, u8>(&promise_indices)
        .unwrap()
        .into_iter()
        .map(|bytes| u64::from_le_bytes(*bytes));

    let mut receipt_dependencies = vec![];
    for promise_idx in promise_indices {
        let promise = ctx
            .promises
            .get(promise_idx as usize)
            .ok_or(HostError::InvalidPromiseIndex { promise_idx })?;
        match &promise {
            Promise::Receipt(receipt_idx) => {
                receipt_dependencies.push(*receipt_idx);
            }
            Promise::NotReceipt(receipt_indices) => {
                receipt_dependencies.extend(receipt_indices.clone());
            }
        }
        // Checking this in the loop to prevent abuse of too many joined vectors.
        if receipt_dependencies.len() as u64
            > ctx.config.limit_config.max_number_input_data_dependencies
        {
            return Err(HostError::NumberInputDataDependenciesExceeded {
                number_of_input_data_dependencies: receipt_dependencies.len() as u64,
                limit: ctx.config.limit_config.max_number_input_data_dependencies,
            }
            .into());
        }
    }
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L142-146)
```rust
#[derive(Debug)]
pub(crate) enum Promise {
    Receipt(ReceiptIndex),
    NotReceipt(Vec<ReceiptIndex>),
}
```
