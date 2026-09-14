### Title
`promise_yield_create_with_id` allows unprivileged callers to collide on user-supplied `YieldId`, enabling front-running DoS of pending cross-contract callbacks - (File: `runtime/runtime/src/ext.rs`)

### Summary
NEAR's yield/resume host API lets a contract create a promise-yield receipt keyed by a caller-influenced, arbitrary 32-byte `YieldId` instead of a runtime-generated identifier. The uniqueness check is scoped only to `(receiver_id, yield_id)`, with no binding to the predecessor/caller that requested the yield. Any account that can call the same contract method can therefore submit a transaction with the same `yield_id` value that another (possibly not-yet-included) transaction is about to use, silently causing the victim's yield creation to fail (`Ok(None)`) — this is the exact "user-provided ID collision → front-running DoS" bug class described in the report, replicated at the protocol host-function layer.

### Finding Description
`create_promise_yield_receipt_with_id` accepts a `receiver_id` and a `user_yield_id` and first checks the trie for an existing mapping before creating the receipt: [1](#0-0) 

The duplicate-detection key is `(receiver_id, yield_id)` only, stored via the `YieldIdToDataId` trie column: [2](#0-1) 

and documented explicitly as "user-provided yield ID" duplicate detection: [3](#0-2) 

Because the `yield_id` is fully attacker-controlled 32 bytes (chosen by whatever contract/dApp logic passes it through from a caller-supplied argument, e.g. an order ID or request ID), and the collision check has no per-caller/predecessor scoping, two different unprivileged callers invoking the same contract method with the same `yield_id` value race for the same slot. The first transaction to be included wins; the second call returns `Ok(None)` from `create_promise_yield_receipt_with_id`, meaning the contract's logic — which relies on this being `Some` to proceed — will treat the caller's operation as rejected/failed. An attacker monitoring the mempool (or simply predicting IDs, e.g. sequential counters or hashes derivable from public information) can pre-empt a victim's transaction with an identical `yield_id`, exactly mirroring the Bridge/TokenDeposit front-running scenario in the external report where a user-chosen ID becomes a DoS vector once claimed by someone else first.

### Impact Explanation
Any dApp built on this primitive that lets external input influence the `yield_id` (a natural design given the API's stated purpose of letting callers supply their own idempotency/tracking ID) is exposed to a transaction-triggered denial of service: a legitimate user's cross-contract callback/yield is silently dropped from creation once an attacker front-runs with the same ID. Depending on how the calling contract handles the `None` result, this can permanently strand escrowed funds/callbacks tied to that yield (e.g. a payment or unlock flow that depends on the yield/resume completing), which falls under "permanently frozen funds" / "transaction-triggered halt" of the affected operation. The vulnerability is reachable by any ordinary transaction signer or RPC caller with no elevated privileges — it only requires calling a public contract method that forwards a caller-influenced ID to `promise_yield_create_with_id`.

### Likelihood Explanation
The host function's duplicate check is unconditional and always public to any account that can invoke a contract method exposing this API, and the report's premise ("malicious users monitor pending transactions and front-run with the same ID") is directly applicable to public mempool visibility on NEAR. The chief mitigating factor is that the actual security impact depends on downstream contract design decisions (whether the `yield_id` is caller-influenced and whether callers can predict/observe each other's IDs), so likelihood is contract-dependent rather than universal, but the protocol-level primitive itself provides zero protection against ID squatting/front-running, unlike `create_promise_yield_receipt` (the ID-less variant) which generates a collision-free ID internally via `generate_data_id()`.

### Recommendation
- For `create_promise_yield_receipt_with_id`, scope the uniqueness key to include the predecessor/caller account (e.g. `(receiver_id, predecessor_id, yield_id)`) so that a caller can only collide with their own prior in-flight yields, not with other unrelated callers.
- Alternatively, document prominently (and enforce at the SDK level) that `yield_id` must be namespaced/salted with the caller's account ID or a runtime-generated component before being passed to this host function, so dApps cannot inadvertently expose a purely user-controlled global ID space.
- Consider returning a distinguishable error (rather than a silent `Ok(None)`) so contract authors are forced to explicitly handle and are less likely to silently treat a collision as an authorization/DoS-prone code path.

### Proof of Concept
1. Contract `C` implements a method `request(order_id: [u8;32])` that calls `promise_yield_create_with_id(receiver_id=C, user_yield_id=order_id)` to track a pending cross-contract operation keyed by `order_id` (a natural pattern since the API is explicitly designed to accept "user-provided" IDs, per `dependencies.rs` docstring).
2. Victim Alice submits transaction `T1` calling `C.request(order_id=X)`.
3. Attacker observes `T1` in the mempool (public), and submits `T2` calling `C.request(order_id=X)` with higher gas price / same or earlier inclusion.
4. Whichever transaction is applied first successfully creates the yield (`has_yield_id_mapping` returns `false`, entry stored via `set_yield_id_mapping` in `runtime/runtime/src/ext.rs:390`).
5. The second transaction's call to `create_promise_yield_receipt_with_id` finds `has_yield_id_mapping == true` and returns `Ok(None)` (`runtime/runtime/src/ext.rs:381-385`), causing contract `C` to reject/fail Alice's legitimate `request` call — a targeted, transaction-triggered denial of service enabled solely by the collision-prone, caller-controlled `YieldId` design.

### Citations

**File:** runtime/runtime/src/ext.rs (L374-403)
```rust
    fn create_promise_yield_receipt_with_id(
        &mut self,
        receiver_id: AccountId,
        user_yield_id: YieldId,
    ) -> Result<Option<(ReceiptIndex, CryptoHash)>, VMLogicError> {
        // Check for duplicate yield_id in trie. TrieUpdate also reflects writes from earlier
        // calls within the same function call, so this also catches in-transaction duplicates.
        if has_yield_id_mapping(self.trie_update, &receiver_id, user_yield_id)
            .map_err(wrap_storage_error)?
        {
            return Ok(None);
        }

        let input_data_id = self.generate_data_id();

        // Store bidirectional yield_id <-> data_id mappings
        set_yield_id_mapping(&mut self.trie_update, &receiver_id, user_yield_id, input_data_id);

        let receipt_index =
            self.receipt_manager.create_promise_yield_receipt(input_data_id, receiver_id.clone());

        set_promise_yield_status(
            &mut self.trie_update,
            &receiver_id,
            input_data_id,
            PromiseYieldStatus::Yielded,
        );

        Ok(Some((receipt_index, input_data_id)))
    }
```

**File:** core/primitives/src/trie_key.rs (L282-290)
```rust
    /// Mapping from user-provided yield ID to runtime-generated data ID.
    /// Used by `promise_yield_create_with_id` for duplicate detection.
    YieldIdToDataId {
        receiver_id: AccountId,
        yield_id: YieldId,
    } = col::YIELD_ID_TO_DATA_ID,
    /// Reverse mapping from runtime-generated data ID to user-provided yield ID.
    /// Used to clean up `YieldIdToDataId` when a yield is resumed or times out.
    DataIdToYieldId {
```

**File:** runtime/near-vm-runner/src/logic/dependencies.rs (L246-260)
```rust
    /// Create a PromiseYield action receipt with a user-provided yield ID.
    ///
    /// Returns `Some((ReceiptIndex, data_id))` of the newly created receipt on success, or
    /// `None` if a yield with the same `user_yield_id` is already pending for this account.
    /// The yield_id -> data_id mapping is stored in the trie for duplicate detection.
    ///
    /// # Arguments
    ///
    /// * `receiver_id` - account id of the receiver of the receipt created
    /// * `user_yield_id` - user-provided 32-byte yield identifier
    fn create_promise_yield_receipt_with_id(
        &mut self,
        receiver_id: AccountId,
        user_yield_id: YieldId,
    ) -> Result<Option<(ReceiptIndex, CryptoHash)>, VMLogicError>;
```
