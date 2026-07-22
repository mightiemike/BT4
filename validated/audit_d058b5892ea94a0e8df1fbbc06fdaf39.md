### Title
Gateway declare-permission checks (`block_declare`, `authorized_declarer_accounts`) enforced only on the RPC/gateway path, silently bypassed on the consensus proposal-validation path — (`crates/apollo_gateway/src/gateway.rs`)

---

### Summary

The `check_declare_permissions` function in `GenericGateway::add_tx_inner` enforces two operator-level admission controls: the `block_declare` flag (temporarily blocks all declare transactions) and the `authorized_declarer_accounts` allowlist (restricts which accounts may declare contracts). These checks are applied exclusively on the RPC/gateway ingress path. The consensus proposal-validation path — `handle_proposal_part` → `convert_consensus_tx_to_internal_consensus_tx` → `send_txs_for_proposal` — applies neither check. A Byzantine proposer can include a `Declare` transaction from an unauthorized account (or when `block_declare = true`) directly in a block proposal; honest validators will convert, forward to the batcher, and execute it without ever consulting the declare-permission gate.

---

### Finding Description

**Restricted path (RPC/gateway):**

`GenericGateway::add_tx_inner` calls `check_declare_permissions` before any conversion or execution:

```
add_tx_inner
  └─ check_declare_permissions          ← block_declare + authorized_declarer_accounts
  └─ stateless_tx_validator.validate
  └─ convert_rpc_tx_to_internal_and_executable_txs
  └─ stateful_transaction_validator.extract_state_nonce_and_run_validations
  └─ mempool_client.add_tx
```

`check_declare_permissions` rejects if `block_declare == true` or if `sender_address ∉ authorized_declarer_accounts`.

**Unrestricted path (consensus proposal validation):**

```
handle_proposal_part (validate_proposal.rs)
  └─ transaction_converter.convert_consensus_tx_to_internal_consensus_tx
       └─ convert_rpc_tx_to_internal          ← NO check_declare_permissions
                                               ← NO stateless_tx_validator.validate
  └─ batcher.send_txs_for_proposal
       └─ blockifier executes __validate_declare__ (signature only)
```

`convert_rpc_tx_to_internal` only compiles the Sierra class and verifies the compiled class hash; it does not consult `block_declare` or `authorized_declarer_accounts`.

The invariant broken: *"If `authorized_declarer_accounts` is set, only those accounts can declare new contracts"* — the exact sequencer-level restriction — is enforced on one path (RPC) but not the other (consensus), mirroring the external bug where `_validateTransfer` was enforced on `transfer`/`transferFrom` but not on `mint`/`burn`.

---

### Impact Explanation

A Byzantine proposer (any validator node whose turn it is to propose) can:

1. Construct a `ConsensusTransaction::RpcTransaction(RpcTransaction::Declare(V3 { sender_address: UNAUTHORIZED, … }))` directly, without going through the gateway.
2. Broadcast it as part of a `ProposalPart::Transactions` batch over the consensus P2P channel.
3. Honest validators receive it in `handle_proposal_part`, convert it via `convert_consensus_tx_to_internal_consensus_tx` (no permission check), and forward it to the batcher via `send_txs_for_proposal`.
4. The blockifier runs `__validate_declare__` (ECDSA signature check only — which the proposer satisfies because it controls the key for `UNAUTHORIZED`).
5. The class is declared and committed to chain state, bypassing `authorized_declarer_accounts`.

When `block_declare = true` the same path allows any declare to be committed despite the operator's intent to halt all declarations (e.g., during an emergency freeze).

Impact category: **Critical — unauthorized Starknet transaction accepted through account-authorization logic** (the `authorized_declarer_accounts` gate is the authorization boundary); and **High — RPC/gateway admission control is rendered ineffective** for the `block_declare` flag.

---

### Likelihood Explanation

- Any validator is eligible to be a proposer in round-robin or leader-election order; no special privilege beyond being a validator is required.
- The attacker only needs to control the private key of an account not in `authorized_declarer_accounts` — a trivially satisfied condition for any account they own.
- The attack requires no malformed bytes, no invalid signatures, and no external dependencies; it is a pure path-selection bypass.

---

### Recommendation

Apply the declare-permission gate in the consensus validation path. The most surgical fix is to add an equivalent check inside `handle_proposal_part` (or a dedicated consensus-side validator) before forwarding transactions to the batcher:

```rust
// In handle_proposal_part, after conversion, before send_txs_for_proposal:
for tx in &txs {
    if let InternalConsensusTransaction::RpcTransaction(ref rpc_tx) = tx {
        if let InternalRpcTransactionWithoutTxHash::Declare(ref decl) = rpc_tx.tx {
            if config.block_declare {
                return HandledProposalPart::Invalid("Declare transactions are blocked".into());
            }
            if !config.is_authorized_declarer(&decl.sender_address) {
                return HandledProposalPart::Invalid(
                    format!("Unauthorized declarer: {}", decl.sender_address)
                );
            }
        }
    }
}
```

Alternatively, centralise the check in `convert_consensus_tx_to_internal_consensus_tx` by threading the gateway config into the `TransactionConverter`, so the same gate is enforced regardless of which path calls the converter.

---

### Proof of Concept

**Setup:**
- Gateway config: `authorized_declarer_accounts = Some([0x1])`, `block_declare = false`.
- Attacker controls validator node `V_evil` and account `0x2` (not in the allowlist).

**Steps:**

1. `V_evil` becomes the proposer for height H.
2. `V_evil` constructs:
   ```
   ConsensusTransaction::RpcTransaction(
       RpcTransaction::Declare(RpcDeclareTransaction::V3(RpcDeclareTransactionV3 {
           sender_address: 0x2,   // NOT in authorized_declarer_accounts
           contract_class: <valid Sierra class>,
           compiled_class_hash: <matching CASM hash>,
           signature: <valid ECDSA sig from key of 0x2>,
           …
       }))
   )
   ```
3. `V_evil` streams this as a `ProposalPart::Transactions` batch to all validators.
4. Each honest validator enters `handle_proposal_part`:
   - Calls `transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)`.
   - Inside `convert_rpc_tx_to_internal`: compiles the class, verifies compiled class hash — **no call to `check_declare_permissions`**.
   - Calls `batcher.send_txs_for_proposal(…)`.
5. The blockifier runs `__validate_declare__` — signature is valid → passes.
6. The class is declared and the state diff is committed.

**Divergent value:** The on-chain class registry now contains a class declared by `0x2`, which `authorized_declarer_accounts` was supposed to prevent. The gateway would have returned `UnauthorizedDeclare` for the identical transaction submitted via RPC.

**Relevant code locations:**

- Permission check applied on RPC path only: [1](#0-0) 
- `check_declare_permissions` implementation: [2](#0-1) 
- Consensus path — no permission check before batcher: [3](#0-2) 
- `convert_consensus_tx_to_internal_consensus_tx` — no permission check: [4](#0-3) 
- `convert_rpc_tx_to_internal` — only compiles class, no permission check: [5](#0-4) 
- Config fields defining the gate: [6](#0-5) 
- `is_authorized_declarer` logic: [7](#0-6)

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L228-233)
```rust
        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }
```

**File:** crates/apollo_gateway/src/gateway.rs (L407-433)
```rust
    fn check_declare_permissions(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> Result<(), StarknetError> {
        // TODO(noamsp): Return same error as in Python gateway.
        if self.config.static_config.block_declare {
            return Err(StarknetError {
                code: StarknetErrorCode::UnknownErrorCode(
                    "StarknetErrorCode.BLOCKED_TRANSACTION_TYPE".to_string(),
                ),
                message: "Transaction type is temporarily blocked.".to_string(),
            });
        }
        let RpcDeclareTransaction::V3(declare_v3_tx) = declare_tx;
        if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) {
            return Err(StarknetError {
                code: StarknetErrorCode::KnownErrorCode(
                    KnownStarknetErrorCode::UnauthorizedDeclare,
                ),
                message: format!(
                    "Account address {} is not allowed to declare contracts.",
                    &declare_v3_tx.sender_address
                ),
            });
        }
        Ok(())
    }
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L598-646)
```rust
        Some(ProposalPart::Transactions(TransactionBatch { transactions: txs })) => {
            // TODO(guyn): check that the length of txs and the number of batches we receive is not
            // so big it would fill up the memory (in case of a malicious proposal)
            debug!("Received transaction batch with {} txs", txs.len());
            let conversion_results =
                futures::future::join_all(txs.into_iter().map(|tx| {
                    transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)
                }))
                .await
                .into_iter()
                .collect::<Result<Vec<_>, _>>();
            let conversion_results = match conversion_results {
                Ok(results) => results,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to convert transactions. Stopping the build of the current \
                         proposal. {e:?}"
                    ));
                }
            };

            // Separate internal transactions from verification and store proof tasks. Each task
            // verifies the proof and stores it in the proof manager. Tasks are collected
            // and awaited later in the fin case.
            let (txs, tasks): (
                Vec<InternalConsensusTransaction>,
                Vec<Option<VerifyAndStoreProofTask>>,
            ) = conversion_results.into_iter().unzip();
            verify_and_store_proof_tasks.extend(tasks.into_iter().flatten());

            debug!(
                "Converted transactions to internal representation. hashes={:?}",
                txs.iter().map(|tx| tx.tx_hash()).collect::<Vec<TransactionHash>>()
            );

            content.push(txs.clone());
            let input = SendTxsForProposalInput { proposal_id, txs };
            let response = match batcher.send_txs_for_proposal(input).await {
                Ok(response) => response,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to send transactions to batcher: {e:?}"
                    ));
                }
            };
            match response {
                SendTxsForProposalStatus::Processing => HandledProposalPart::Continue,
                SendTxsForProposalStatus::InvalidProposal(err) => HandledProposalPart::Invalid(err),
            }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L184-202)
```rust
    async fn convert_consensus_tx_to_internal_consensus_tx(
        &self,
        tx: ConsensusTransaction,
    ) -> TransactionConverterResult<(InternalConsensusTransaction, Option<VerifyAndStoreProofTask>)>
    {
        match tx {
            ConsensusTransaction::RpcTransaction(tx) => {
                let (internal_tx, proof_data) = self.convert_rpc_tx_to_internal(tx).await?;
                let task = proof_data.map(|(proof_facts, proof)| {
                    self.spawn_verify_and_store_proof(proof_facts, proof)
                });
                Ok((InternalConsensusTransaction::RpcTransaction(internal_tx), task))
            }
            ConsensusTransaction::L1Handler(tx) => {
                let internal_tx = self.convert_consensus_l1_handler_to_internal_l1_handler(tx)?;
                Ok((InternalConsensusTransaction::L1Handler(internal_tx), None))
            }
        }
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L334-392)
```rust
    async fn convert_rpc_tx_to_internal(
        &self,
        tx: RpcTransaction,
    ) -> TransactionConverterResult<(InternalRpcTransaction, Option<(ProofFacts, Proof)>)> {
        let (tx_without_hash, proof_data) = match tx {
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => {
                let proof_data = if tx.proof_facts.is_empty() {
                    None
                } else {
                    Some((tx.proof_facts.clone(), tx.proof.clone()))
                };
                (InternalRpcTransactionWithoutTxHash::Invoke(tx.into()), proof_data)
            }
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
                // TODO(Aviv): Ensure that we do not want to
                // allow declare with compiled class hash v1.
                if tx.compiled_class_hash != executable_class_hash_v2 {
                    return Err(TransactionConverterError::ValidateCompiledClassHashError(
                        ValidateCompiledClassHashError::CompiledClassHashMismatch {
                            computed_class_hash: executable_class_hash_v2,
                            supplied_class_hash: tx.compiled_class_hash,
                        },
                    ));
                }
                (
                    InternalRpcTransactionWithoutTxHash::Declare(InternalRpcDeclareTransactionV3 {
                        sender_address: tx.sender_address,
                        compiled_class_hash: tx.compiled_class_hash,
                        signature: tx.signature,
                        nonce: tx.nonce,
                        class_hash,
                        resource_bounds: tx.resource_bounds,
                        tip: tx.tip,
                        paymaster_data: tx.paymaster_data,
                        account_deployment_data: tx.account_deployment_data,
                        nonce_data_availability_mode: tx.nonce_data_availability_mode,
                        fee_data_availability_mode: tx.fee_data_availability_mode,
                    }),
                    None,
                )
            }
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                let contract_address = tx.calculate_contract_address()?;
                (
                    InternalRpcTransactionWithoutTxHash::DeployAccount(
                        InternalRpcDeployAccountTransaction {
                            tx: RpcDeployAccountTransaction::V3(tx),
                            contract_address,
                        },
                    ),
                    None,
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```

**File:** crates/apollo_gateway_config/src/config.rs (L49-51)
```rust
    pub block_declare: bool,
    #[serde(default, deserialize_with = "deserialize_comma_separated_str")]
    pub authorized_declarer_accounts: Option<Vec<ContractAddress>>,
```

**File:** crates/apollo_gateway_config/src/config.rs (L141-146)
```rust
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
        }
    }
```
