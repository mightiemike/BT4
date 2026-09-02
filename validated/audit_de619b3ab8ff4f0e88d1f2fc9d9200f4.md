### Title
Stale council-signed `BatchProofMethodId` can be replayed to permanently block the genuine upgrade - (File: `crates/light-client-prover/src/circuit/mod.rs`)

### Summary
The `BatchProofMethodId` acceptance rule in `run_l1_block` only checks that `activation_l2_height` is monotonically greater than the *last inserted* value, without binding the check to a specific, currently-intended council authorization (no nonce, sequence number, or "supersedes" reference in the signed payload). Any previously council-signed `BatchProofMethodIdBody` (for an activation height that was later superseded by a different, final proposal) remains cryptographically valid forever and, if inscribed on Bitcoin ahead of the genuine transaction, permanently occupies the "last activation height" slot and causes the genuine, currently-intended upgrade to be skipped.

### Finding Description
The binding the code is supposed to enforce is: *the `(activation_l2_height, method_id)` accepted into `BatchProofMethodIdAccessor` equals the value the council most recently and specifically authorised for the currently pending upgrade*. What the code actually enforces is only:

```rust
let last_activation_height = batch_proof_method_ids.last().expect("Should be at least one").0;
if batch_proof_method_id.body.activation_l2_height <= last_activation_height {
    continue;
}
``` [1](#0-0) 

Signature verification (`verify_method_id_security_council`) is performed over `body.serialize()`, i.e. over `(method_id, activation_l2_height, chain_id)` only [2](#0-1) [3](#0-2) . There is no nonce, proposal id, or reference to a prior/expected `last_activation_height` inside the signed message. Consequently, once the security council has ever produced a validly-signed `BatchProofMethodId` for some `activation_l2_height = Y` (e.g. an earlier draft/candidate that was later superseded by a different final proposal for `H_genuine`), that exact signed blob remains permanently valid and can be re-inscribed by anyone holding its bytes (no forging required — the attacker only needs to mine/relay an already-signed message, which the stated attacker capabilities explicitly allow: "pay Bitcoin fees to inscribe or mine any Bitcoin transaction").

`BatchProofMethodIdAccessor::insert` unconditionally appends whatever the last accepted body was [4](#0-3) , and the "last" element (not "max") is what gates the next acceptance. If an attacker gets the stale `Y`-height body mined in an earlier DA block/earlier tx position than the genuine `H_genuine` transaction, and `Y >= H_genuine`, `BatchProofMethodIdAccessor` is updated to `(Y, method_id_stale)`. When the genuine transaction for `H_genuine` is later processed, `H_genuine <= last_activation_height (Y)` is true, so it hits `continue` and is permanently skipped — permanently, because `last_activation_height` only grows and there is no mechanism to revert or re-attempt with the same or lower height.

Existing guards (`chain_id` check, `verify_method_id_security_council`) do not prevent this because the replayed body is a genuinely and correctly signed council message — it is just stale/superseded, and the protocol has no notion of "superseded."

### Impact Explanation
If exploited, the light client prover state permanently freezes on the stale/attacker-chosen method_id/activation height, and the actually-intended council upgrade for `H_genuine` can never be applied (it will always evaluate `activation_l2_height <= last_activation_height`). This matches the "Critical: a true state-transition/upgrade made unprovable/unappliable" category — for example, if the genuine upgrade was meant to patch a vulnerable batch-proof method id, the vulnerable method id would remain active indefinitely, and the fix becomes permanently unappliable through this DA channel. The attack is a one-shot event per targeted upgrade (it must precede the genuine transaction in DA ordering) but its effect persists for the lifetime of the chain unless a hard fork / new initial values are deployed.

### Likelihood Explanation
This requires a specific precondition: the attacker must possess the raw bytes of a validly council-signed `BatchProofMethodId` body for some height `Y >= H_genuine` that the council itself produced but did not use (e.g., a draft/candidate signed during coordination, later replaced by a different final proposal). This is not forgeable by the attacker and does not come from any capability enumerated for the unprivileged attacker (EVM tx, deposit tx, arbitrary Bitcoin tx bytes they already hold) unless such a stale signed artifact is externally leaked or exposed (e.g., broadcast to the Bitcoin mempool and later replaced/dropped, or shared during multi-party signing coordination before finalization). Given the current in-repo evidence, there is no protocol mechanism (nonce/expiry/supersession reference) preventing this, but the practical likelihood strongly depends on the security council's own operational hygiene (whether stale signed candidates are ever exposed), which is outside the code under audit.

### Recommendation
Bind each `BatchProofMethodIdBody` to a strictly-increasing, single-use identifier (e.g., include the *exact* expected `previous_activation_l2_height`/sequence number the council observed when signing, or a monotonic proposal nonce) inside the signed payload, and require `run_l1_block` to check the message references the current `last_activation_height` value exactly rather than any historical value satisfying only `>`. This makes each council signature valid for one specific state transition only, eliminating replay of superseded proposals.

### Proof of Concept
```rust
// crates/light-client-prover/src/tests/mod.rs (conceptual addition)
#[test]
fn stale_signed_method_id_blocks_genuine_upgrade() {
    // 1. Council signs body_stale = { activation_l2_height: 300, method_id: OLD_VULN_ID }
    //    (simulating a superseded draft) using create_new_method_id_tx / create_valid_signatures.
    // 2. Council later signs body_genuine = { activation_l2_height: 250, method_id: FIXED_ID }
    //    as the actual intended, final upgrade (H_genuine=250 < 300).
    // 3. Build da_txs = [blob(body_stale), blob(body_genuine)] in that DA order.
    // 4. Run run_l1_block over these da_txs.
    // 5. Assert BatchProofMethodIdAccessor::get(...) contains (300, OLD_VULN_ID) as last entry,
    //    and does NOT contain (250, FIXED_ID) -- i.e. the genuine, currently-authorised
    //    upgrade was skipped ("not greater than the last one"), confirming
    //    last_activation_height (300, OLD_VULN_ID) != the council's currently intended
    //    (250, FIXED_ID) binding.
}
```

### Citations

**File:** crates/light-client-prover/src/circuit/mod.rs (L529-543)
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

**File:** crates/light-client-prover/src/circuit/method_id_verifier.rs (L14-30)
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
