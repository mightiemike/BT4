### Title
Silent `proof_facts` Default in `IntermediateInvokeTransaction`→`InvokeTransactionV3` Conversion Produces Wrong Transaction Hash for Client-Side Proving Transactions - (File: `crates/apollo_starknet_client/src/reader/objects/transaction.rs`)

### Summary
The `IntermediateInvokeTransaction`→`InvokeTransactionV3` conversion silently defaults `proof_facts` to empty when the field is absent from the feeder-gateway/RPC response. Because `get_invoke_transaction_v3_hash` conditionally appends the `proof_facts` hash to the preimage **only when non-empty**, any path that recomputes the hash from the deserialized body will produce a hash that diverges from the canonical on-chain hash for client-side-proving transactions. This is the direct sequencer analog of the bonding-curve invariant break: a value that was non-zero at commit time is silently initialized to zero at read time, causing the derived invariant (hash identity) to fail.

### Finding Description

**Step 1 – The conditional hash domain.**

`get_invoke_transaction_v3_hash` in `crates/starknet_api/src/transaction_hash.rs` appends `proof_facts` to the Poseidon preimage only when the field is non-empty:

```rust
if !transaction.proof_facts().0.is_empty() {
    let proof_facts_hash =
        HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
    hash_chain = hash_chain.chain(&proof_facts_hash);
}
```

A transaction submitted with non-empty `proof_facts` therefore has a canonical hash `H_real` that includes the proof-facts contribution. A structurally identical transaction with empty `proof_facts` produces a different hash `H_empty ≠ H_real`.

**Step 2 – The silent default in the reader conversion.**

`IntermediateInvokeTransaction`→`InvokeTransactionV3` in `crates/apollo_starknet_client/src/reader/objects/transaction.rs` uses:

```rust
// proof_facts is optional for backward compatibility with V3 transactions created
// before client-side proving was added. Old V3 txs don't have this field.
proof_facts: invoke_tx.proof_facts.unwrap_or_default(),
```

When the feeder-gateway or RPC response omits `proof_facts` (the field is only returned when the caller includes `response_flags: ["INCLUDE_PROOF_FACTS"]` per Starknet RPC v0.10), `unwrap_or_default()` silently substitutes `ProofFacts::default()` (empty). The deserialized `InvokeTransactionV3` now carries empty `proof_facts` even though the on-chain transaction had non-empty `proof_facts`.

**Step 3 – The same silent default in the storage backward-compatibility shim.**

The custom `StorageSerde` impl for `InvokeTransactionV3` in `crates/apollo_storage/src/serialization/serializers.rs` applies the same pattern:

```rust
// Backward compatibility: proof_facts may not exist in old transactions.
// If no data remains, default to empty; otherwise, deserialize normally.
let proof_facts = if data.is_empty() {
    ProofFacts::default()
} else {
    ProofFacts::deserialize_from(data)?
};
```

A transaction that was stored with non-empty `proof_facts` but whose on-disk bytes are truncated or misread as "no remaining data" would also be silently reconstructed with empty `proof_facts`.

**Step 4 – Hash recomputation paths that consume the wrong value.**

Any code path that calls `calculate_transaction_hash` on the deserialized object will compute `H_empty` instead of `H_real`. Concrete paths include:

- `convert_rpc_tx_to_internal` in `crates/apollo_transaction_converter/src/transaction_converter.rs`, which calls `tx_without_hash.calculate_transaction_hash(&self.chain_id)?` and stores the result as the authoritative `tx_hash` in `InternalRpcTransaction`.
- `validate_transaction_hash` in `crates/starknet_api/src/transaction_hash.rs`, which recomputes the hash from the transaction body and checks it against the expected value.
- Blockifier re-execution (`crates/blockifier_reexecution`), which fetches transactions from the RPC and re-executes them; the transaction hash is exposed to contracts via the `get_execution_info` syscall.

### Impact Explanation

**Impact: High** — "Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."

When `proof_facts` is silently dropped:

1. `convert_rpc_tx_to_internal` stores `H_empty` as the canonical hash. Every downstream component (mempool deduplication, batcher, consensus, storage) operates on the wrong hash.
2. `validate_transaction_hash` recomputes `H_empty` and fails to find it in the set of valid hashes for the transaction, causing valid transactions to be rejected.
3. During blockifier re-execution, the contract's `get_execution_info` syscall returns `H_empty` instead of `H_real`, producing a divergent execution trace and wrong receipt/event output.

### Likelihood Explanation

**Likelihood: Medium.** The `proof_facts` field is only included in feeder-gateway/RPC responses when the caller explicitly requests it via `response_flags: ["INCLUDE_PROOF_FACTS"]`. State-sync nodes, re-execution tooling, and any integration that does not set this flag will silently receive empty `proof_facts` for every client-side-proving transaction. As client-side proving (SNIP-36) is actively deployed, the population of affected transactions grows over time.

### Recommendation

1. **Fail loudly on absent `proof_facts` when the stored hash is available.** After deserialization, recompute the hash and assert it matches the stored hash. If it does not, return an error rather than silently accepting the wrong value.
2. **Always request `INCLUDE_PROOF_FACTS` in feeder-gateway/RPC calls** made by state-sync and re-execution paths so the field is never absent for V3 transactions.
3. **Remove the `unwrap_or_default()` fallback** once all nodes have re-synced and the backward-compatibility window has closed, replacing it with an explicit error on missing `proof_facts` for transactions whose stored hash implies a non-empty value.

### Proof of Concept

```
1. User submits InvokeTransactionV3 with proof_facts = [PROOF_V1, VIRTUAL_SNOS, ...]
   → gateway computes H_real = poseidon(INVOKE || version || sender || ... || proof_facts_hash)
   → transaction stored in block with tx_hash = H_real

2. State-sync node fetches the transaction from feeder gateway WITHOUT
   response_flags: ["INCLUDE_PROOF_FACTS"]
   → IntermediateInvokeTransaction.proof_facts = None
   → unwrap_or_default() → proof_facts = ProofFacts::default() (empty)

3. Node calls calculate_transaction_hash on the deserialized body:
   → get_invoke_transaction_v3_hash skips the proof_facts branch (is_empty == true)
   → H_empty = poseidon(INVOKE || version || sender || ...)   ← missing proof_facts_hash
   → H_empty ≠ H_real

4. validate_transaction_hash(tx, block_number, chain_id, expected_hash=H_real):
   → recomputes H_empty, checks possible_hashes.contains(&H_real) → false
   → returns Ok(false) / validation failure for a valid on-chain transaction

5. Blockifier re-execution executes the transaction with tx_hash = H_empty:
   → get_execution_info syscall returns H_empty to the contract
   → execution trace diverges from the canonical trace produced at sequencing time
   → wrong receipt, events, and state diff committed
```

<cite repo="Oyahkilomeikhide/sequencer--024" path="crates/apollo_starknet_client/src/reader/objects/transaction