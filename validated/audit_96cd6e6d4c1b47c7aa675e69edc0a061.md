### Title
`DelegateAction` Signed Payload Lacks `chain_id` Binding, Enabling Cross-Chain Replay of Meta-Transactions — (`core/primitives/src/action/delegate.rs`, `runtime/runtime/src/actions.rs`)

---

### Summary

`DelegateAction` (NEP-366 meta-transactions) commits no chain identifier into its signed payload. A malicious relayer who receives a user's `SignedDelegateAction` intended for NEAR mainnet can submit it verbatim on NEAR testnet (or any NEAR fork sharing the same keypair state), causing the inner actions to execute on the wrong chain and draining the user's balance there.

---

### Finding Description

`DelegateAction` is the user-signed inner payload of a meta-transaction. Its struct definition contains no `chain_id` field:

```rust
pub struct DelegateAction {
    pub sender_id: AccountId,
    pub receiver_id: AccountId,
    pub actions: Vec<NonDelegateAction>,
    pub nonce: Nonce,
    pub max_block_height: BlockHeight,
    pub public_key: PublicKey,
    // ← no chain_id
}
``` [1](#0-0) 

The NEP-461 signing hash is computed over this struct with only a message-type discriminant prepended — no chain context is included:

```rust
pub fn get_nep461_hash(&self) -> CryptoHash {
    let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
    let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
    hash(&bytes)
}
``` [2](#0-1) 

At execution time, `apply_delegate_action` verifies the signature, checks expiry, validates the sender, and checks the nonce — but never asserts that the action was intended for the current chain:

```rust
pub(crate) fn apply_delegate_action(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    action_receipt: &VersionedActionReceipt,
    sender_id: &AccountId,
    signed_delegate_action: VersionedSignedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    if !signed_delegate_action.verify() { ... }          // sig check — no chain_id
    if apply_state.block_height > delegate_action.max_block_height() { ... }  // expiry
    if delegate_action.sender_id().as_str() != sender_id.as_str() { ... }     // sender
    validate_delegate_action_key(...)?;                  // nonce + key perms
    // ← no chain_id check anywhere
``` [3](#0-2) 

The `apply_state` passed into this function carries `block_height`, `shard_id`, `current_protocol_version`, and other context, but the chain's identity (its genesis `chain_id`, e.g. `"mainnet"` vs `"testnet"`) is never threaded in and never checked. [4](#0-3) 

By contrast, the **outer** `SignedTransaction` wrapping the `DelegateAction` is chain-bound via its `block_hash` field, which is validated through `validity_period_validate_is_ancestor` → `InvalidChain`. But this only protects the relayer's outer transaction; the inner user-signed payload carries no such binding. [5](#0-4) 

---

### Impact Explanation

A malicious relayer who receives a user's `SignedDelegateAction` off-chain can wrap it in a fresh outer transaction and submit it on any NEAR-based chain where:

1. The user's `sender_id` account exists with the same public key.
2. The stored nonce for that key is lower than the `DelegateAction`'s nonce.
3. The `max_block_height` has not yet been exceeded on the target chain.

All three conditions are routinely satisfied between NEAR mainnet and testnet (developers commonly reuse keypairs), and are guaranteed to hold immediately after a chain fork (the fork inherits the full account state). The inner actions — which may include `Transfer`, `FunctionCall`, `AddKey`, `DeleteAccount` — execute with `sender_id` as `predecessor_id`, so any contract that gates on `predecessor_id` (e.g. a fungible-token contract checking `env::predecessor_account_id()`) will treat the call as legitimately from the user. The user's balance on the target chain is drained without their consent.

---

### Likelihood Explanation

Low. The attack requires a malicious relayer (the user must have chosen to send their signed payload to an adversary), and the user must hold the same keypair on the target chain with a usable nonce. These conditions are uncommon in normal usage but are realistic for developers who share keys across mainnet/testnet, and are guaranteed to hold for any NEAR fork that copies existing account state.

---

### Recommendation

Include the chain's genesis `chain_id` string in the `DelegateAction` signed payload (analogous to EIP-155 for Ethereum). Concretely:

1. Add a `chain_id: String` field to `DelegateAction` (and `DelegateActionV2`).
2. Require relayers to populate it with the chain's genesis `chain_id` before the user signs.
3. In `apply_delegate_action`, after loading `apply_state`, assert `delegate_action.chain_id == apply_state.chain_id` and return `ActionErrorKind::DelegateActionInvalidChainId` on mismatch.

Because `chain_id` is part of the Borsh-serialized payload fed to `get_nep461_hash`, any signature produced for one chain will fail `signed_delegate_action.verify()` on a different chain.

---

### Proof of Concept

```
1. Alice holds keypair K on both NEAR mainnet and NEAR testnet.
   Mainnet nonce for K: 50.  Testnet nonce for K: 10.

2. Alice creates and signs a DelegateAction on mainnet:
     DelegateAction {
       sender_id: "alice.near",
       receiver_id: "bob.near",
       actions: [Transfer { deposit: 100 NEAR }],
       nonce: 51,
       max_block_height: mainnet_height + 1000,
       public_key: K.public,
     }
   Signature = sign(K.secret, NEP461_hash(above))

3. Alice sends the SignedDelegateAction to relayer R off-chain.

4. R is malicious. R constructs a fresh outer transaction on TESTNET:
     SignedTransaction {
       signer_id: R_testnet,
       receiver_id: "alice.near",   // routes to Alice's testnet shard
       block_hash: <recent testnet block>,
       actions: [Delegate(SignedDelegateAction from step 2)],
     }
   R signs this outer tx with R's testnet key.

5. R submits the outer tx to a testnet RPC node.

6. Testnet runtime calls apply_delegate_action:
   - signed_delegate_action.verify() → TRUE  (same key, same payload hash)
   - block_height <= max_block_height → TRUE  (testnet height < mainnet height + 1000)
   - sender_id == "alice.near" → TRUE
   - validate_delegate_action_key: nonce 51 > current 10 → TRUE
   - New receipt created: Transfer 100 NEAR from alice.near to bob.near on TESTNET.

7. Alice's testnet account loses 100 NEAR. She never authorized this on testnet.
```

The outer transaction's `block_hash` chain-ancestry check passes because it references a valid testnet block. The inner `DelegateAction` signature passes because it contains no chain discriminator. The nonce advances on testnet, permanently consuming nonce slot 51 there.

### Citations

**File:** core/primitives/src/action/delegate.rs (L46-64)
```rust
pub struct DelegateAction {
    /// Signer of the delegated actions
    pub sender_id: AccountId,
    /// Receiver of the delegated actions.
    pub receiver_id: AccountId,
    /// List of actions to be executed.
    ///
    /// With the meta transactions MVP defined in NEP-366, nested
    /// DelegateActions are not allowed. A separate type is used to enforce it.
    pub actions: Vec<NonDelegateAction>,
    /// Nonce to ensure that the same delegate action is not sent twice by a
    /// relayer and should match for given account's `public_key`.
    /// After this action is processed it will increment.
    pub nonce: Nonce,
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```

**File:** core/primitives/src/action/delegate.rs (L353-357)
```rust
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
        let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
        hash(&bytes)
    }
```

**File:** runtime/runtime/src/actions.rs (L422-491)
```rust
pub(crate) fn apply_delegate_action(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    action_receipt: &VersionedActionReceipt,
    sender_id: &AccountId,
    signed_delegate_action: VersionedSignedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    if !signed_delegate_action.verify() {
        result.result = Err(ActionErrorKind::DelegateActionInvalidSignature.into());
        return Ok(());
    }
    let delegate_action = signed_delegate_action.delegate_action();
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
    if delegate_action.sender_id().as_str() != sender_id.as_str() {
        result.result = Err(ActionErrorKind::DelegateActionSenderDoesNotMatchTxReceiver {
            sender_id: delegate_action.sender_id().clone(),
            receiver_id: sender_id.clone(),
        }
        .into());
        return Ok(());
    }

    validate_delegate_action_key(state_update, apply_state, delegate_action, result)?;
    if result.result.is_err() {
        // Validation failed. Need to return Ok() because this is not a runtime error.
        // "result.result" will be return to the User as the action execution result.
        return Ok(());
    }

    // Generate a new receipt from DelegateAction.
    let new_receipt = Receipt::V0(ReceiptV0 {
        predecessor_id: sender_id.clone(),
        receiver_id: delegate_action.receiver_id().clone(),
        receipt_id: CryptoHash::default(),

        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: action_receipt.signer_id().clone(),
            signer_public_key: action_receipt.signer_public_key().clone(),
            gas_price: action_receipt.gas_price(),
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: delegate_action.get_actions(),
        }),
    });

    // Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas.
    // If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction.
    // Gas is refunded to the signer, this is Relayer.
    // Some contracts refund the deposit. Usually they refund the deposit to the predecessor and this is sender_id/Sender from DelegateAction.
    // Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit.

    let prepaid_send_fees = total_prepaid_send_fees(&apply_state.config, action_receipt.actions())?;
    let required_cost = receipt_required_cost(apply_state, &new_receipt)?;
    // This gas will be burnt by the receiver of the created receipt.
    // Compute costs of that are not relevant at this point, the "used" gas is
    // only reserved for execution later, potentially on a different shard.
    result.gas_used = result.gas_used.checked_add_result(required_cost.gas)?;
    // This gas was prepaid on Relayer shard. Need to burn it because the receipt is going to be sent.
    // gas_used is incremented because otherwise the gas will be refunded. Refund function checks only gas_used.
    result.gas_used = result.gas_used.checked_add_result(prepaid_send_fees.gas)?;
    result.gas_burnt = result.gas_burnt.checked_add_result(prepaid_send_fees.gas)?;
    result.compute_usage = safe_add_compute(result.compute_usage, prepaid_send_fees.compute)?;
    result.new_receipts.push(new_receipt);

    Ok(())
}
```

**File:** runtime/runtime/src/lib.rs (L727-748)
```rust
            Action::Delegate(signed_delegate_action) => {
                metrics::ACTION_CALLED_COUNT.delegate.inc();
                apply_delegate_action(
                    state_update,
                    apply_state,
                    action_receipt,
                    account_id,
                    signed_delegate_action.as_ref().into(),
                    &mut result,
                )?;
            }
            Action::DelegateV2(signed_delegate_action) => {
                metrics::ACTION_CALLED_COUNT.delegate.inc();
                apply_delegate_action(
                    state_update,
                    apply_state,
                    action_receipt,
                    account_id,
                    signed_delegate_action.as_ref().into(),
                    &mut result,
                )?;
            }
```

**File:** chain/chain/src/store/utils.rs (L130-177)
```rust
fn validity_period_validate_is_ancestor(
    base_header: &BlockHeader,
    prev_block_header: &BlockHeader,
    chain_store: &ChainStoreAdapter,
) -> Result<(), InvalidTxError> {
    let base_height = base_header.height();
    let prev_height = prev_block_header.height();
    let base_block_hash = base_header.hash();

    // Base can't be an ancestor of prev if its height is bigger
    if base_height > prev_height {
        return Err(InvalidTxError::InvalidChain);
    }

    // if both are on the canonical chain, comparing height is sufficient
    // we special case this because it is expected that this scenario will happen in most cases.
    if let Ok(base_block_hash_by_height) = chain_store.get_block_hash_by_height(base_height) {
        if &base_block_hash_by_height == base_block_hash {
            if let Ok(prev_hash) = chain_store.get_block_hash_by_height(prev_height) {
                if &prev_hash == prev_block_header.hash() {
                    return Ok(());
                }
            }
        }
    }

    // if the base block height is smaller than `last_final_height` we only need to check
    // whether the base block is the same as the one with that height on the canonical fork.
    // Otherwise we walk back the chain to check whether base block is on the same chain.
    let last_final_height = chain_store
        .get_block_height(prev_block_header.last_final_block())
        .map_err(|_| InvalidTxError::InvalidChain)?;

    if last_final_height >= base_height {
        let base_block_hash_by_height = chain_store
            .get_block_hash_by_height(base_height)
            .map_err(|_| InvalidTxError::InvalidChain)?;
        if &base_block_hash_by_height == base_block_hash {
            Ok(())
        } else {
            Err(InvalidTxError::InvalidChain)
        }
    } else {
        let header =
            get_block_header_on_chain_by_height(chain_store, prev_block_header.hash(), base_height)
                .map_err(|_| InvalidTxError::InvalidChain)?;
        if header.hash() == base_block_hash { Ok(()) } else { Err(InvalidTxError::InvalidChain) }
    }
```
