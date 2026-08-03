## Finding

Based on direct inspection of the code, the Rust-side construction of `PrologueArgs::V1` **does not enforce or validate positional/length alignment** between `secondary_signer_addresses` and `secondary_signer_public_key_hashes`, and the upstream signature-verification path that produces these vectors does not enforce equal lengths either.

### Title
Unvalidated length mismatch between `secondary_signer_addresses` and `secondary_authentication_proofs` in multi-agent/fee-payer transactions - (File: `types/src/transaction/authenticator.rs`, `aptos-move/aptos-vm/src/transaction_metadata.rs`, `aptos-move/aptos-vm/src/transaction_validation_versioned.rs`)

### Summary
`TransactionAuthenticator::verify` for the `MultiAgent` and `FeePayer` variants never asserts that `secondary_signer_addresses.len() == secondary_signers.len()`. It only iterates over the actual `secondary_signers` authenticator list and checks each entry's signature against a fixed signing message (which embeds the full `secondary_signer_addresses` list as signed content, not as a per-index pairing check): [1](#0-0) 

This means a transaction where `secondary_signer_addresses` has a different length than `secondary_signers` (e.g. more addresses listed than actual signer authenticators supplied, or vice versa) can pass `verify()` — nothing in this function rejects the length mismatch.

`TransactionAuthenticator::secondary_signer_addresses()` and `secondary_signers()` are independent accessors that simply return whatever vectors are stored in the enum, with no cross-validation: [2](#0-1) 

`TransactionMetadata::new` then builds `secondary_signers` (addresses) and `secondary_authentication_proofs` from these two independently-sized vectors: [3](#0-2) 

Finally, `PrologueBuilder::new` builds `secondary_signer_addresses` and `secondary_signer_public_key_hashes` directly from `txn_data.secondary_signers()` and `txn_data.secondary_authentication_proofs` respectively — again with no length assertion or `zip`-based truncation — and BCS-serializes both into `PrologueArgs::V1`: [4](#0-3) [5](#0-4) 

### Finding Description
An attacker crafting a `MultiAgent` or `FeePayer` signed transaction can supply `secondary_signer_addresses` and `secondary_signers` (the authenticator list) of different lengths, e.g. listing an address with no corresponding authenticator entry, and this passes `TransactionAuthenticator::verify` because the verification loop only walks the `secondary_signers` authenticator list, not the address list. This mismatch survives unchanged into `TransactionMetadata` and then into `PrologueArgs::V1`, which is passed as opaque BCS bytes to the Move `VERSIONED_PROLOGUE_NAME` entry function.

### Impact Explanation
I confirmed the Rust layer performs no length/positional validation at any point in this pipeline — `TransactionAuthenticator::verify`, `TransactionMetadata::new`, and `PrologueBuilder::new`/`build` all pass the mismatched vectors through untouched. I was **not able to fully verify**, within the scope of this review, how `transaction_validation.move`'s Move-side prologue function consumes `secondary_signer_addresses` versus `secondary_signer_public_key_hashes` when their lengths differ (i.e., whether it aborts safely on out-of-bounds vector access, or whether it silently truncates/defaults and thereby skips the authentication-key check for a listed secondary signer while still granting that account's `&signer` capability to the executed entry function). That Move-side behavior is the deciding factor for whether this defect can actually corrupt committed state (misbinding a `&signer` capability to an unauthenticated address) versus merely causing the transaction to abort safely (a liveness-only outcome, out of scope per the state-integrity gate).

### Likelihood Explanation
Constructing a mismatched-length multi-agent/fee-payer transaction is fully within unprivileged attacker control (no special access needed — the attacker fully controls what they submit as a raw transaction and its authenticator vectors), and I confirmed it passes Rust-side verification without rejection.

### Recommendation
Add an explicit length-equality assertion between `secondary_signer_addresses` and `secondary_signers`/`secondary_authentication_proofs` at the earliest possible point — ideally inside `TransactionAuthenticator::verify` for `MultiAgent`/`FeePayer` (rejecting non-conforming transactions before they are admitted to mempool or executed), and defensively again in `PrologueBuilder::new` before building `PrologueArgs::V1`, regardless of what the Move-side code currently does.

### Proof of Concept
As suggested in the question, add a unit test around `PrologueBuilder::new`/`build` (in `aptos-move/aptos-vm/src/transaction_validation_versioned.rs`) constructing a `TransactionMetadata` whose `secondary_signers` (addresses) vector has a different length than `secondary_authentication_proofs`, and assert that `build()` either panics/returns an error or that the resulting `PrologueArgs::V1`'s two vectors remain equal-length. Currently `build()` will happily serialize the mismatched-length vectors with no check, confirming the missing invariant at this boundary: [6](#0-5) 

**Caveat:** I could not fully confirm, using the tools available in this session, whether `transaction_validation.move` on the Move side independently re-validates and rejects this length mismatch (which would downgrade this to a safe-abort/DoS-only issue) or whether it silently misaligns/grants signer capability (which would be a genuine state-integrity break). A Devin session with full file access to `aptos-move/framework/aptos-framework/sources/transaction_validation.move` would be needed to conclusively determine end-to-end impact.

### Citations

**File:** types/src/transaction/authenticator.rs (L229-243)
```rust
            Self::MultiAgent {
                sender,
                secondary_signer_addresses,
                secondary_signers,
            } => {
                let message = RawTransactionWithData::new_multi_agent(
                    raw_txn_for_signing.into_owned(),
                    secondary_signer_addresses.clone(),
                );
                sender.verify(&message)?;
                for signer in secondary_signers {
                    signer.verify(&message)?;
                }
                Ok(())
            },
```

**File:** types/src/transaction/authenticator.rs (L264-299)
```rust
    pub fn secondary_signer_addresses(&self) -> Vec<AccountAddress> {
        match self {
            Self::Ed25519 { .. } | Self::MultiEd25519 { .. } | Self::SingleSender { .. } => {
                vec![]
            },
            Self::FeePayer {
                sender: _,
                secondary_signer_addresses,
                ..
            } => secondary_signer_addresses.to_vec(),
            Self::MultiAgent {
                sender: _,
                secondary_signer_addresses,
                ..
            } => secondary_signer_addresses.to_vec(),
        }
    }

    pub fn secondary_signers(&self) -> Vec<AccountAuthenticator> {
        match self {
            Self::Ed25519 { .. } | Self::MultiEd25519 { .. } | Self::SingleSender { .. } => {
                vec![]
            },
            Self::FeePayer {
                sender: _,
                secondary_signer_addresses: _,
                secondary_signers,
                ..
            } => secondary_signers.to_vec(),
            Self::MultiAgent {
                sender: _,
                secondary_signer_addresses: _,
                secondary_signers,
            } => secondary_signers.to_vec(),
        }
    }
```

**File:** aptos-move/aptos-vm/src/transaction_metadata.rs (L134-140)
```rust
            secondary_signers: txn.authenticator().secondary_signer_addresses(),
            secondary_authentication_proofs: txn
                .authenticator()
                .secondary_signers()
                .iter()
                .map(|account_auth| account_auth.authentication_proof())
                .collect(),
```

**File:** aptos-move/aptos-vm/src/transaction_validation_versioned.rs (L65-95)
```rust
impl PrologueBuilder {
    pub fn new(
        serialized_signers: &SerializedSigners,
        txn_data: &TransactionMetadata,
        is_simulation: bool,
    ) -> Self {
        Self {
            needs_fee_payer_auth_check: serialized_signers.fee_payer().is_some(),
            txn_sender_public_key: txn_data.authentication_proof().optional_auth_key(),
            fee_payer_public_key_hash: txn_data
                .fee_payer_authentication_proof
                .as_ref()
                .and_then(|proof| proof.optional_auth_key()),
            replay_protector: txn_data.replay_protector(),
            secondary_signer_addresses: txn_data.secondary_signers(),
            secondary_signer_public_key_hashes: txn_data
                .secondary_authentication_proofs
                .iter()
                .map(|proof| proof.optional_auth_key())
                .collect(),
            txn_gas_price: txn_data.gas_unit_price().into(),
            txn_max_gas_units: txn_data.max_gas_amount().into(),
            txn_expiration_time: txn_data.expiration_timestamp_secs(),
            chain_id: txn_data.chain_id().id(),
            is_simulation,
            txn_limits_request: txn_data.txn_limits.as_ref().and_then(|v| match v {
                TxnLimitsRequest::ApprovedGovernanceScript => None,
                TxnLimitsRequest::Staking(req) => Some(req.clone()),
            }),
        }
    }
```

**File:** aptos-move/aptos-vm/src/transaction_validation_versioned.rs (L99-115)
```rust
    pub fn build(self) -> Vec<u8> {
        let args = PrologueArgs::V1 {
            needs_fee_payer_auth_check: self.needs_fee_payer_auth_check,
            txn_sender_public_key: self.txn_sender_public_key,
            fee_payer_public_key_hash: self.fee_payer_public_key_hash,
            replay_protector: self.replay_protector,
            secondary_signer_addresses: self.secondary_signer_addresses,
            secondary_signer_public_key_hashes: self.secondary_signer_public_key_hashes,
            txn_gas_price: self.txn_gas_price,
            txn_max_gas_units: self.txn_max_gas_units,
            txn_expiration_time: self.txn_expiration_time,
            chain_id: self.chain_id,
            is_simulation: self.is_simulation,
            txn_limits_request: self.txn_limits_request,
        };
        bcs::to_bytes(&args).expect("Failed to serialize prologue arguments")
    }
```
