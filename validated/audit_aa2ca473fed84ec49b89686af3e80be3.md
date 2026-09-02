### Title
Out-of-order confirmation of council-signed `BatchProofMethodId` bodies permanently drops earlier-height upgrades, making true batch proofs unprovable - (File: crates/light-client-prover/src/circuit/mod.rs)

### Summary
`run_l1_block`'s handling of `DataOnDa::BatchProofMethodId` gates insertion solely on `batch_proof_method_id.body.activation_l2_height <= last_activation_height`, where `last_activation_height` is `BatchProofMethodIdAccessor::get(...).last().0` — the height of whichever entry was inserted most recently, not the maximum authorized height. Because insertion order is driven by DA block/transaction order rather than by the council-assigned `activation_l2_height`, an attacker who reorders confirmation of two already fully-signed council bodies (H1 < H2) can cause the H2 body to land first, which then permanently blocks the legitimate H1 body via the `continue` on line 539-542 of `crates/light-client-prover/src/circuit/mod.rs`.

### Finding Description
The binding this code is supposed to enforce is:
`BatchProofMethodIdAccessor::get() ⊇ { every (activation_l2_height, method_id) that received ≥3-of-5 valid security council signatures for the running chain_id }`.

Tracing `run_l1_block` (`crates/light-client-prover/src/circuit/mod.rs:529-566`):
- For `DataOnDa::BatchProofMethodId`, there is no `blob.sender()` check at all — authenticity relies purely on `verify_method_id_security_council` validating 3-of-5 ECDSA signatures over `batch_proof_method_id.body.serialize()` [1](#0-0) . This means the *carrier* Bitcoin transaction is irrelevant; any party can copy the exact signed bytes of a body that a council member already broadcast into their own inscription/transaction.
- Before signature verification, the code reads `last_activation_height` as `batch_proof_method_ids.last().0` [2](#0-1)  and rejects (`continue`, no error, no revert) any body whose `activation_l2_height` is `<=` that value [3](#0-2) .
- `BatchProofMethodIdAccessor::insert` simply `push`es to the end of the vector [4](#0-3) , so the vector's ordering — and thus what `.last()` returns — is determined entirely by *processing order* (DA block height, then transaction position within the block), not by the value of `activation_l2_height`.

Attacker flow: two genuine, fully-signed bodies for H1 and H2 (H1<H2) exist (e.g. council pre-signs and broadcasts both for a scheduled multi-stage upgrade). The attacker copies the byte-identical H2 inscription payload into their own Bitcoin transaction with a higher fee, causing it to confirm in an earlier L1 block (or earlier position in the same block) than the original H1 transaction. When the light client processes blocks sequentially:
1. H2's blob is processed first: `activation_l2_height(H2) <= last_activation_height` is false (initial state), signatures verify, `insert(H2, M2)` succeeds.
2. H1's blob is processed later: `last_activation_height` is now `H2`; since `H1 <= H2`, the branch hits `continue` before signature verification even runs, silently dropping the authorized H1 entry forever — there is no re-insertion path and no error surfaced.

No existing guard prevents this: `verify_method_id_security_council` only checks authenticity of content, not ordering; `blob.sender()` is not checked for this variant; there is no nonce, previous-body-hash, or monotonic-height commitment that ties H2's authorization to having been preceded by H1's insertion.

### Impact Explanation
Once H1 is dropped, `BatchProofMethodIdAccessor::get()` never contains `(H1, M1)`. In `process_complete_proof`, the `binary_search_by_key` over `batch_proof_method_ids` [5](#0-4)  resolves any L2 height in `[H1, H2)` to the wrong method id (the pre-H1 id, since H1's entry is missing), so `Z::verify` with that wrong id will reject any genuine batch proof built with `M1` for that L2 range [6](#0-5) . This matches the Critical category "a true [proof] made unprovable": batch proofs for the entire L2 range `[H1, H2)` become permanently unverifiable by every honest light-client prover following the proved chain, since the light-client circuit's state (the JMT-backed `BatchProofMethodIdAccessor`) is deterministic and shared — there is no way to retroactively re-insert H1 once superseded. This is a chain-wide, node-independent failure (every honest LCP instance reaches the same broken state), not merely a local node issue.

### Likelihood Explanation
The attack requires: (1) the security council to have already produced and broadcast/exposed two valid signed bodies for different future heights before both are confirmed (an operational precondition, not attacker-controlled) — the question stipulates this precondition explicitly; (2) the attacker only needs to observe already-public signed payload bytes and pay a higher Bitcoin fee to get their copy mined first, well within reach of an unprivileged attacker with no special keys. The chain-id and signature checks do not defend against this because they authenticate content, not order or carrier transaction. Given the precondition holds, the reordering itself is cheap and deterministic (just fee-bumping), making the attack highly feasible whenever the council pipelines more than one upgrade at a time.

### Recommendation
Do not gate on `last()`/append order. Store `BatchProofMethodIds` sorted by `activation_l2_height`, and check for existing/duplicate/overlapping entries by height value rather than by vector position. When inserting a newly authorized body, insert it at the correct sorted position (or reject only if an entry for that exact height already exists), and re-validate that no already-authorized lower-height entry gets silently skipped due to processing order. Alternatively, bind consecutive upgrade bodies together (e.g., include the previous activation height/method id or a monotonically increasing nonce in the signed message) so out-of-order confirmation is cryptographically detectable and can be handled deterministically rather than silently dropped.

### Proof of Concept
```rust
// crates/light-client-prover/src/circuit/mod.rs (or a new test in crates/light-client-prover/src/tests)
// 1. Generate two valid council-signed BatchProofMethodIdBody blobs:
//    body_h1 = { activation_l2_height: H1, method_id: M1, chain_id }
//    body_h2 = { activation_l2_height: H2, method_id: M2, chain_id }, H1 < H2
//    Sign both with 3-of-5 valid security council signatures (using existing
//    `generate_initial_pub_keys_with_signers` / `create_valid_signatures` test helpers).
// 2. Feed them to run_l1_block in reverse order (H2 blob processed before H1 blob),
//    either within the same da_txs vector or across two sequential run_l1_block calls
//    simulating two DA blocks.
// 3. Assert:
assert!(BatchProofMethodIdAccessor::<S>::get(&mut working_set)
    .unwrap()
    .iter()
    .any(|(h, m)| *h == H1 && *m == M1) == false); // H1 entry missing -> vulnerability confirmed
assert!(BatchProofMethodIdAccessor::<S>::get(&mut working_set)
    .unwrap()
    .iter()
    .any(|(h, m)| *h == H2 && *m == M2)); // H2 entry present
```
This demonstrates that the legitimately authorized `(H1, M1)` entry never lands in state once `(H2, M2)` is processed first, confirming the binding break with no mainnet or live Clementine dependency.

### Citations

**File:** crates/light-client-prover/src/circuit/mod.rs (L288-303)
```rust
        let batch_proof_method_ids = BatchProofMethodIdAccessor::<S>::get(working_set)
            .expect("Batch proof method ids must exist");

        let batch_proof_method_id = if batch_proof_method_ids.len() == 1 {
            batch_proof_method_ids[0].1
        } else {
            let idx = match batch_proof_method_ids
                // Returns err and the index to be inserted, which is the index of the first element greater than the key
                // That is why we need to subtract 1 to get the last element smaller than the key
                .binary_search_by_key(&batch_proof_output_last_l2_height, |(height, _)| *height)
            {
                Ok(idx) => idx,
                Err(idx) => idx.saturating_sub(1),
            };
            batch_proof_method_ids[idx].1
        };
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L307-312)
```rust
        Z::verify(
            proof,
            &batch_proof_method_id.into(),
            network_to_dev_mode(network),
        )
        .map_err(|_| "Failed to verify proof")?;
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L531-537)
```rust
                    let batch_proof_method_ids =
                        BatchProofMethodIdAccessor::<S>::get(&mut working_set).unwrap();

                    let last_activation_height = batch_proof_method_ids
                        .last()
                        .expect("Should be at least one")
                        .0;
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L539-542)
```rust
                    if batch_proof_method_id.body.activation_l2_height <= last_activation_height {
                        log!("Batch proof method id activation height is not greater than the last one");
                        continue;
                    }
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L552-559)
```rust
                    if !verify_method_id_security_council(
                        *method_id_upgrade_authority_da_public_keys,
                        batch_proof_method_id.body.serialize().as_slice(),
                        batch_proof_method_id.signatures_with_index(),
                    ) {
                        log!("Method ID security council verification failed");
                        continue;
                    }
```

**File:** crates/light-client-prover/src/circuit/accessors.rs (L319-327)
```rust
    pub fn insert(activation_l2_height: u64, method_id: [u32; 8], working_set: &mut WorkingSet<S>) {
        let key = Self::key();
        let mut method_ids = Self::get(working_set).unwrap_or_default();
        method_ids.push((activation_l2_height, method_id));
        let value: StorageValue = borsh::to_vec(&method_ids)
            .expect("Batch proof method ids serialization should not fail")
            .into();
        working_set.set(&key, value);
    }
```
