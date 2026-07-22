### Title
Gateway stateless validator omits `allowed_proof_versions`/`allowed_virtual_os_program_hashes` whitelist checks enforced by blockifier pre-validation — (`crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

The gateway's `StatelessTransactionValidator` checks only whether client-side proving is enabled (`allow_client_side_proving`), but does not validate that the proof version or program hash embedded in `proof_facts` appears in the `allowed_proof_versions` or `allowed_virtual_os_program_hashes` lists from `VersionedConstants`. Those checks exist exclusively in blockifier's `validate_proof_facts`, called during `perform_pre_validation_stage`. A transaction carrying a cryptographically valid proof but a disallowed proof version or program hash passes gateway admission, enters the mempool, and fails blockifier pre-validation on every execution attempt — without ever charging fees.

### Finding Description

**Admission-time check (gateway):**

`StatelessTransactionValidator::validate_client_side_proving_allowed` only tests the boolean flag:

```rust
fn validate_client_side_proving_allowed(
    &self,
    tx: &RpcInvokeTransaction,
) -> StatelessTransactionValidatorResult<()> {
    if self.config.allow_client_side_proving {
        return Ok(());   // ← accepted unconditionally when flag is true
    }
    let RpcInvokeTransaction::V3(tx) = tx;
    let has_proof_data = !tx.proof_facts.is_empty() || !tx.proof.is_empty();
    if has_proof_data {
        return Err(StatelessTransactionValidatorError::ClientSideProvingNotAllowed);
    }
    Ok(())
}
``` [1](#0-0) 

`StatelessTransactionValidatorConfig` carries `allow_client_side_proving` and `max_proof_size`, but has no `allowed_proof_versions` or `allowed_virtual_os_program_hashes` fields. The gateway therefore has no mechanism to enforce the versioned-constants whitelist at admission time. [2](#0-1) 

**Execution-time check (blockifier):**

`AccountTransaction::validate_proof_facts` enforces the whitelist during `perform_pre_validation_stage`:

```rust
if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt()) {
    return Err(TransactionPreValidationError::InvalidProofFacts(...));
}
let allowed = &os_constants.allowed_virtual_os_program_hashes;
if !allowed.contains(&snos_proof_facts.program_hash) {
    return Err(TransactionPreValidationError::InvalidProofFacts(...));
}
``` [3](#0-2) 

These lists are versioned constants that differ across protocol versions:

| Version | `allowed_proof_versions` | `allowed_virtual_os_program_hashes` |
|---|---|---|
| ≤ 0.13.x | `[]` | `[]` |
| 0.14.2 | `["0x50524f4f4630"]` (V0) | one hash |
| 0.14.3 | `["0x50524f4f4631"]` (V1) | different hash |
| 0.14.4 | `["0x50524f4f4631"]` (V1) | two hashes | [4](#0-3) [5](#0-4) 

**The gap:** `verify_proof` (called asynchronously by the gateway converter) validates the cryptographic proof against the proof facts, but does not consult `allowed_proof_versions` or `allowed_virtual_os_program_hashes`:

```rust
pub fn reconstruct_output_preimage(proof_facts: &ProofFacts) -> Result<Vec<Felt>, VerifyProofError> {
    // Skip PROOF_VERSION_V* (index 0) and variant (index 1).
    let task_content = &proof_facts.0[2..];
    ...
}
``` [6](#0-5) 

A transaction with proof version V0 submitted against a node running v0.14.3 (which only allows V1) will:
1. Pass `verify_proof` — the proof is cryptographically valid.
2. Pass `validate_client_side_proving_allowed` — the flag is true.
3. Enter the mempool.
4. Fail `validate_proof_facts` in blockifier on every block attempt with `"Proof version … is not allowed under this protocol version."` — a pre-validation error that excludes the transaction without charging fees.

### Impact Explanation

**High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

Any unprivileged user who can generate a valid proof (e.g., using the public prover endpoint) can submit Invoke V3 transactions whose `proof_facts` carry a proof version or program hash outside the current `VersionedConstants` whitelist. The gateway admits them; the blockifier permanently rejects them at zero cost to the attacker. The mempool accumulates permanently-failing transactions, degrading throughput for legitimate users and wasting batcher resources on transactions that can never be sequenced.

### Likelihood Explanation

**Medium.** The precondition is `allow_client_side_proving = true` at the gateway (a production configuration for nodes supporting client-side proving). The attacker must produce a cryptographically valid proof — feasible via the public prover — but can use any proof version or program hash value, including ones from a prior or future protocol version. No privileged access is required.

### Recommendation

Mirror the blockifier's whitelist checks in `StatelessTransactionValidatorConfig` and `StatelessTransactionValidator`:

1. Add `allowed_proof_versions: Vec<Felt>` and `allowed_virtual_os_program_hashes: Vec<Felt>` to `StatelessTransactionValidatorConfig`, populated from the node's active `VersionedConstants`.
2. In `validate_client_side_proving_allowed` (or a new `validate_proof_facts_content`), parse the proof version and program hash from `proof_facts` and reject the transaction if either is absent from the respective whitelist — mirroring the logic in `AccountTransaction::validate_proof_facts`.

This closes the version/config boundary gap between gateway admission and blockifier pre-validation, consistent with the existing pattern of pre-empting OS-level rejections at the gateway (e.g., `validate_empty_account_deployment_data`, `validate_nonce_data_availability_mode`). [7](#0-6) [8](#0-7) 

### Proof of Concept

1. Run a sequencer node with `allow_client_side_proving = true` and `VersionedConstants` v0.14.3 (only allows proof version V1 = `0x50524f4f4631`).
2. Generate a valid proof using the prover, but embed proof version V0 (`0x50524f4f4630`) as the first field of `proof_facts`.
3. Submit the Invoke V3 transaction via the HTTP gateway or JSON-RPC `starknet_addInvokeTransaction`.
4. **Observed:** gateway returns success; transaction hash is assigned; transaction appears in mempool.
5. **Observed:** batcher repeatedly attempts to include the transaction; blockifier raises `TransactionPreValidationError::InvalidProofFacts("Proof version PROOF0 is not allowed under this protocol version.")` on every attempt; transaction is excluded from every block without fee charge.
6. **Expected:** gateway should reject the transaction at step 3 with an `UNSUPPORTED_TX_VERSION`-equivalent error, consistent with how `validate_nonce_data_availability_mode` pre-empts OS-level DA-mode rejections. [9](#0-8) [10](#0-9)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L31-54)
```rust
impl StatelessTransactionValidator {
    #[instrument(skip(self), level = Level::INFO)]
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L100-118)
```rust
    /// The Starknet OS enforces that the deployer data is empty. We add this validation here in the
    /// gateway to prevent transactions from failing the OS.
    fn validate_empty_account_deployment_data(
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let account_deployment_data = match tx {
            RpcTransaction::DeployAccount(_) => return Ok(()),
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => &tx.account_deployment_data,
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => &tx.account_deployment_data,
        };

        if account_deployment_data.is_empty() {
            Ok(())
        } else {
            Err(StatelessTransactionValidatorError::NonEmptyField {
                field_name: "account_deployment_data".to_string(),
            })
        }
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L197-212)
```rust
    /// The Starknet OS enforces that the nonce data availability mode is L1. We add this validation
    /// here in the gateway to prevent transactions from failing the OS.
    fn validate_nonce_data_availability_mode(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let expected_da_mode = DataAvailabilityMode::L1;
        let da_mode = *tx.nonce_data_availability_mode();
        if da_mode != expected_da_mode {
            return Err(StatelessTransactionValidatorError::InvalidDataAvailabilityMode {
                field_name: "nonce".to_string(),
            });
        };

        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L231-263)
```rust
    fn validate_client_side_proving_allowed(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if self.config.allow_client_side_proving {
            return Ok(());
        }

        // Reject V3 transactions with proofs when client-side proving is disabled.
        let RpcInvokeTransaction::V3(tx) = tx;
        let has_proof_data = !tx.proof_facts.is_empty() || !tx.proof.is_empty();
        if has_proof_data {
            return Err(StatelessTransactionValidatorError::ClientSideProvingNotAllowed);
        }

        Ok(())
    }

    fn validate_proof_facts_and_proof_consistency(
        &self,
        tx: &RpcInvokeTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let RpcInvokeTransaction::V3(tx) = tx;
        let has_proof_facts = !tx.proof_facts.is_empty();
        let has_proof = !tx.proof.is_empty();
        if has_proof_facts != has_proof {
            return Err(StatelessTransactionValidatorError::ProofFactsAndProofConsistency {
                has_proof_facts,
                has_proof,
            });
        }
        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L291-351)
```rust
    fn validate_proof_facts(
        &self,
        block_context: &BlockContext,
        state: &mut dyn State,
    ) -> TransactionPreValidationResult<()> {
        // Only Invoke V3 transactions can carry proof facts.
        let Transaction::Invoke(invoke_tx) = &self.tx else {
            return Ok(());
        };
        if invoke_tx.version() < TransactionVersion::THREE {
            return Ok(());
        }

        // Parse proof facts.
        let proof_facts = invoke_tx.proof_facts();
        let snos_proof_facts = match ProofFactsVariant::try_from(&proof_facts)
            .map_err(|e| TransactionPreValidationError::InvalidProofFacts(e.to_string()))?
        {
            ProofFactsVariant::Empty => return Ok(()),
            ProofFactsVariant::Snos(snos_proof_facts) => snos_proof_facts,
        };
        let os_constants = &block_context.versioned_constants.os_constants;

        if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
        {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof version {} is not allowed under this protocol version.",
                snos_proof_facts.proof_version
            )));
        }

        // Validate the program hash.
        let allowed = &os_constants.allowed_virtual_os_program_hashes;
        if !allowed.contains(&snos_proof_facts.program_hash) {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS program hash {} is not allowed",
                snos_proof_facts.program_hash
            )));
        }

        // Validate the block hash and block number.
        let proof_block_hash = snos_proof_facts.block_hash.0;
        let proof_block_number = snos_proof_facts.block_number.0;
        Self::validate_proof_block_number(
            proof_block_number,
            block_context.block_info.block_number,
        )?;
        Self::validate_proof_block_hash(proof_block_hash, proof_block_number, os_constants, state)?;

        // Validate the config hash.
        let virtual_os_config_hash = block_context.virtual_os_config_hash();
        let proof_config_hash = snos_proof_facts.config_hash;
        if virtual_os_config_hash != proof_config_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS config hash mismatch. Computed virtual OS config hash: \
                 {virtual_os_config_hash}, expected virtual OS config hash: {proof_config_hash}."
            )));
        }

        Ok(())
    }
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_3.json (L128-134)
```json
    "os_constants": {
        "allowed_virtual_os_program_hashes": [
            "0x53f6c9fcfd31d27279ff7d7e422b44623550a732b59fe193354a7316a96daa1"
        ],
        "allowed_proof_versions": [
            "0x50524f4f4631"
        ],
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_4.json (L128-135)
```json
    "os_constants": {
        "allowed_virtual_os_program_hashes": [
            "0x53f6c9fcfd31d27279ff7d7e422b44623550a732b59fe193354a7316a96daa1",
            "0x1c7be3225dfb33359b3ba9ffbe1542b7da27879b6d89f47b967e512463fd324"
        ],
        "allowed_proof_versions": [
            "0x50524f4f4631"
        ],
```

**File:** crates/starknet_proof_verifier/src/proof_verifier.rs (L108-121)
```rust
pub fn reconstruct_output_preimage(
    proof_facts: &ProofFacts,
) -> Result<Vec<Felt>, VerifyProofError> {
    // Proof facts must contain at least [PROOF_VERSION_V*, variant, program_hash].
    if proof_facts.0.len() < 3 {
        return Err(VerifyProofError::ProofFactsTooShort { length: proof_facts.0.len() });
    }
    // Skip PROOF_VERSION_V* (index 0) and variant (index 1).
    let task_content = &proof_facts.0[2..];
    let output_size = Felt::from(
        u64::try_from(task_content.len() + 1).expect("task content length exceeds u64::MAX"),
    );
    Ok([Felt::ONE, output_size].into_iter().chain(task_content.iter().copied()).collect())
}
```
