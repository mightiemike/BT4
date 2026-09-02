### Title
Stale but validly council-signed `BatchProofMethodId` can be front-run ahead of the real upgrade, making an unintended method id authoritative for a range of L2 heights - (File: `crates/light-client-prover/src/circuit/mod.rs`)

### Summary
`run_l1_block`'s handling of `DataOnDa::BatchProofMethodId` authenticates a method-id update purely by its embedded 3-of-5 council signature and a strictly-increasing `activation_l2_height` check against whatever is *currently* stored, with no sender check, nonce, or "supersedes" binding. Because insertion order is driven by DA submission order (which anyone can influence simply by choosing when to broadcast, no mining power needed), a previously-signed-but-abandoned council body with a lower `activation_l2_height` can be inserted before the council's real, intended body, making a stale method id briefly authoritative for `[H+1, H+100)`.

### Finding Description
The intended binding is: **for a given `activation_l2_height`, the stored `method_id` in `BatchProofMethodIdAccessor` == the method id the council currently intends to be live at that height.**

In `crates/light-client-prover/src/circuit/mod.rs` (`run_l1_block`, `DataOnDa::BatchProofMethodId` branch, lines 529-566):
```rust
let last_activation_height = batch_proof_method_ids.last().expect("...").0;
if batch_proof_method_id.body.activation_l2_height <= last_activation_height {
    continue;
}
...
if !verify_method_id_security_council(...) { continue; }
BatchProofMethodIdAccessor::<S>::insert(body.activation_l2_height, body.method_id, &mut working_set);
``` [1](#0-0) 

Note that unlike `DataOnDa::Complete`, `Aggregate`, and `SequencerCommitment` (which check `blob.sender()`), this branch performs **no sender check at all** — this is by design, per `crates/bitcoin-da/src/verifier.rs` which explicitly sets the sender to a dummy `[0u8;32]` because "this data and signatures are embedded in the body" [2](#0-1) . Any unprivileged party can inscribe and broadcast any `BatchProofMethodId` blob as long as it carries a valid signature set.

The only anti-replay control is the monotonic height gate (`activation_l2_height <= last_activation_height`), whose own comment states it is meant to prevent replay of *old, already-superseded* method IDs [3](#0-2) . This gate only protects against replaying an id that is smaller than one **already recorded on-chain** — it does nothing to prevent an *not-yet-submitted* stale signed body from being submitted first, since `last_activation_height` is state that mutates based purely on submission order. `BatchProofMethodIdBody` itself carries no nonce, sequence number, or "supersedes" reference — only `method_id`, `activation_l2_height`, and `chain_id` [4](#0-3) , so once 3-of-5 council members have signed a candidate body, that signature remains valid and replayable forever, regardless of whether the council later decided on a different body.

Exploit flow: the council signs body_old (`activation_l2_height=H+1`, `old_method_id`) as a draft/earlier candidate, then supersedes it with body_new (`activation_l2_height=H+100`, `new_method_id`), intending only body_new to ever be broadcast. If body_old's signed bytes become available to any unprivileged party (no key compromise needed — just possession of the previously produced signature bytes, e.g. from an earlier internal draft that leaked or was shared for review), that party inscribes body_old and gets it mined/relayed to the light-client-prover before body_new is submitted. Since `H+1 > last_activation_height` (nothing has been recorded yet) and the signatures verify (they are real), `BatchProofMethodIdAccessor::insert(H+1, old_method_id)` succeeds. When body_new is later processed, `H+100 > H+1` still passes and is also inserted. The result: `batch_proof_method_ids = [..., (H+1, old_method_id), (H+100, new_method_id)]`, so `process_complete_proof`'s `binary_search_by_key` on `batch_proof_output_last_l2_height` [5](#0-4)  will select `old_method_id` for any L2 height in `[H+1, H+100)`, even though the council never intended `old_method_id` to govern that window.

Existing guards do not stop this: `blob.sender()` is not checked for this variant (checked in the SequencerCommitment branch [6](#0-5)  but not here); `verify_method_id_security_council` only checks that 3 valid, ascending, non-duplicate signatures exist over the exact body bytes [7](#0-6)  — it has no notion of "is this the latest decision" and cannot detect supersession.

### Impact Explanation
For L2 heights in `[H+1, H+100)`, batch proofs are verified against `old_method_id` instead of the method id the council currently intends. If the superseded circuit differs meaningfully from the intended one (e.g., it was replaced precisely because of a soundness bug), this allows a batch proof for that window to verify under a circuit the council did not want live there — a proof accepted for a state transition governed by unauthorized circuit logic. This is repeatable for any future upgrade window where a stale, previously fully-signed candidate is available and not yet superseded on-chain, and it affects every full node and light-client prover deriving state from the same L1 data.

### Likelihood Explanation
Requires: (1) the security council to have produced a complete 3-of-5 valid signature set over a candidate body it later decided not to use, and (2) that signed body to become available to a third party before the real update is submitted on-chain. This does not require compromising any private key — only obtaining previously produced, still-cryptographically-valid signature bytes for an abandoned proposal, and paying ordinary Bitcoin inscription fees to broadcast it. Whether this is practically likely depends heavily on council operational hygiene (whether draft/candidate signatures are ever produced before the final body is agreed and whether such material is kept secret); the protocol itself provides no cryptographic safeguard against it.

### Recommendation
Bind each signed `BatchProofMethodIdBody` to being the unique currently-valid proposal, e.g. by including a strictly increasing sequence/nonce number that must equal `previous_sequence + 1` (rather than allowing any greater `activation_l2_height`), so an older signed proposal cannot be inserted after a newer one has been decided regardless of submission order; alternatively, require the body to reference/invalidate the specific prior proposal it supersedes, and/or require signatures to commit to a monotonic "decision timestamp" enforced independently of DA arrival order.

### Proof of Concept
In `crates/light-client-prover/src/tests/mod.rs`, using the existing `create_new_method_id_tx` helper (which produces fully valid council signatures):
```rust
// body_old: legitimately signed by council, activation_l2_height = H+1, method_id = old_id
let blob_old = create_new_method_id_tx(H + 1, old_id, method_id_sender, Network::Nightly);
// body_new: legitimately signed by council, activation_l2_height = H+100, method_id = new_id
let blob_new = create_new_method_id_tx(H + 100, new_id, method_id_sender, Network::Nightly);

// Feed them to run_l1_block/run_circuit in the order [blob_old, blob_new]
// (simulating blob_old being inscribed/relayed before blob_new)
let output = zk_circuit_runner.run_circuit(..., completeness_proof: vec![blob_old, blob_new], ...).unwrap();

let ids = BatchProofMethodIdAccessor::<ProverStorage>::get(&mut working_set).unwrap();
assert_eq!(ids, vec![(0, INITIAL_ID), (H + 1, old_id), (H + 100, new_id)]);
// binary_search_by_key for any last_l2_height in [H+1, H+100) now resolves to old_id,
// even though council's final intent (body_new) says new_id should be the only valid upgrade.
```

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

**File:** crates/light-client-prover/src/circuit/mod.rs (L529-566)
```rust
                DataOnDa::BatchProofMethodId(batch_proof_method_id) => {
                    log!("Found batch proof method id");
                    let batch_proof_method_ids =
                        BatchProofMethodIdAccessor::<S>::get(&mut working_set).unwrap();

                    let last_activation_height = batch_proof_method_ids
                        .last()
                        .expect("Should be at least one")
                        .0;

                    if batch_proof_method_id.body.activation_l2_height <= last_activation_height {
                        log!("Batch proof method id activation height is not greater than the last one");
                        continue;
                    }

                    let circuit_chain_id = citrea_network_to_chain_id(network);
                    if circuit_chain_id != batch_proof_method_id.body.chain_id {
                        log!("Method ID upgrade transactions chain ID does not match circuit chain ID");
                        continue;
                    }

                    // Verify the signatures only if the activation height is greater than the last one
                    // This prevents replay attacks of old method IDs
                    if !verify_method_id_security_council(
                        *method_id_upgrade_authority_da_public_keys,
                        batch_proof_method_id.body.serialize().as_slice(),
                        batch_proof_method_id.signatures_with_index(),
                    ) {
                        log!("Method ID security council verification failed");
                        continue;
                    }

                    BatchProofMethodIdAccessor::<S>::insert(
                        batch_proof_method_id.body.activation_l2_height,
                        batch_proof_method_id.body.method_id,
                        &mut working_set,
                    );
                }
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L567-575)
```rust
                DataOnDa::SequencerCommitment(commitment) => {
                    log!("Found sequencer commitment with index {}", commitment.index);
                    if blob.sender().as_ref() != sequencer_da_public_key {
                        log!(
                            "Sequencer commitment sender is not sequencer, wtxid={:?}",
                            blob.wtxid()
                        );
                        continue;
                    }
```

**File:** crates/bitcoin-da/src/verifier.rs (L153-168)
```rust
                    // The verification of BatchProofMethodId security council signatures are done in the circuit
                    ParsedTransaction::BatchProofMethodId(method_id) => {
                        // Pubkey here is given as 0 because the security council pub keys are inside the body
                        let public_key = [0u8; 32].to_vec();
                        let hash = method_id.hash();

                        blobs.push(BlobWithSender::new(
                            // Body here is: borsh(DataOnDa::BatchProofMethodId(BatchProofMethodId { ... }))
                            // The sender field here is not used because this transaction has a security council
                            // consisting of 5 public keys, this data and signatures are embedded in the body
                            method_id.body,
                            public_key,
                            hash,
                            *wtxid,
                        ))
                    }
```

**File:** crates/sovereign-sdk/rollup-interface/src/state_machine/da.rs (L41-57)
```rust
/// Body of the batch proof method id update for light client
#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq, BorshDeserialize, BorshSerialize)]
pub struct BatchProofMethodIdBody {
    /// New method id of upcoming fork
    pub method_id: [u32; 8],
    /// Activation L2 height of the new method id
    pub activation_l2_height: u64,
    /// Network identifier to prevent cross network replay attacks
    pub chain_id: u64,
}

impl BatchProofMethodIdBody {
    /// Serialize the body using borsh
    pub fn serialize(&self) -> Vec<u8> {
        borsh::to_vec(self).expect("BatchProofMethodIdBody serialization cannot fail")
    }
}
```

**File:** crates/light-client-prover/src/circuit/method_id_verifier.rs (L14-69)
```rust
pub fn verify_method_id_security_council(
    initial_da_pubkeys: [[u8; SECURITY_COUNCIL_COMPRESSED_PUBKEY_SIZE];
        SECURITY_COUNCIL_MEMBER_COUNT],
    msg: &[u8],
    signatures_with_idx: &[([u8; SECURITY_COUNCIL_SIGNATURE_SIZE], u8);
         SECURITY_COUNCIL_SIGNATURE_THRESHOLD],
) -> bool {
    // EIP-191 prefix + keccak256 → 32-byte prehash
    let prehash = eip191_hash_message(msg);

    // Check that signature indices are within bounds
    for &(_, index) in signatures_with_idx {
        if index >= 5 {
            log!("Invalid signature index: {}", index);
            return false;
        }
    }

    // Make sure the indexes are in ascending order to prevent duplicates
    for i in 0..signatures_with_idx.len() - 1 {
        if signatures_with_idx[i].1 >= signatures_with_idx[i + 1].1 {
            log!(
                "Signature indices are not in ascending order, failing indices: {}, {}",
                signatures_with_idx[i].1,
                signatures_with_idx[i + 1].1
            );
            return false;
        }
    }

    for signature_with_idx in signatures_with_idx.iter() {
        let signature = signature_with_idx.0;
        let pubkey_idx = signature_with_idx.1;
        let const_pubkey = initial_da_pubkeys[pubkey_idx as usize];

        // ensure the inscription pubkey matches the expected constant (compressed 33B)
        let verifying_key = VerifyingKey::from_sec1_bytes(const_pubkey.as_slice())
            .expect("Initial DA pubkeys must be parsable to k256 VerifyingKey form sec1 bytes");

        let Ok(parsed_sig) = Signature::from_bytes(&signature.into()) else {
            log!("Invalid signature format");
            return false; // invalid signature format, fail
        };

        // verify prehash with the matching verifying key
        if verifying_key
            .verify_prehash(prehash.as_slice(), &parsed_sig)
            .is_err()
        {
            log!("Signature verification failed for index: {}", pubkey_idx);
            return false;
        }
    }

    true
}
```
