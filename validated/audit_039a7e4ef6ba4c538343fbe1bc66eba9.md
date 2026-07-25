### Title
`DelegateAction` Signed Payload Lacks Chain-ID Binding, Enabling Cross-Network Replay of Meta-Transaction Signatures — (`File: core/primitives/src/action/delegate.rs`, `core/primitives/src/signable_message.rs`)

---

### Summary

The `DelegateAction` (NEP-366 meta transaction) signed payload does not include any chain-specific identifier (chain ID, network ID, or genesis hash). The NEP-461 `SignableMessage` wrapper adds only a protocol-type discriminant (a NEP number). As a result, a `SignedDelegateAction` produced on one NEAR network (e.g., testnet) is cryptographically valid on any other NEAR network (e.g., mainnet) where the same account, key, and a compatible nonce exist. A malicious or compromised relayer can replay the user's off-chain-signed `DelegateAction` on a different network without the user's knowledge or consent.

---

### Finding Description

`DelegateAction` is the struct a user signs to authorize a meta transaction. Its fields are:

```rust
pub struct DelegateAction {
    pub sender_id: AccountId,
    pub receiver_id: AccountId,
    pub actions: Vec<NonDelegateAction>,
    pub nonce: Nonce,
    pub max_block_height: BlockHeight,
    pub public_key: PublicKey,
}
``` [1](#0-0) 

The hash that is actually signed is produced by `get_nep461_hash()`:

```rust
pub fn get_nep461_hash(&self) -> CryptoHash {
    let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
    let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
    hash(&bytes)
}
``` [2](#0-1) 

`SignableMessage` prepends only a `MessageDiscriminant` (a 4-byte NEP number, `1<<30 + 366 = 0x4000016E`):

```rust
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}
``` [3](#0-2) 

The discriminant encodes only the NEP number, not any chain-specific data:

```rust
SignableMessageType::DelegateAction =>
    MessageDiscriminant::new_on_chain(NEP_366_META_TRANSACTIONS).unwrap()
``` [4](#0-3) 

The outer `SignedTransaction` does include a `block_hash` that is chain-specific, but that field is signed by the **relayer**, not the user. The user only signs the `DelegateAction`. The relayer can freely construct a new outer transaction on any network, wrapping the same user-signed `DelegateAction`.

At execution time, `apply_delegate_action` performs these checks:

```rust
if !signed_delegate_action.verify() { ... }          // cryptographic sig check
if apply_state.block_height > delegate_action.max_block_height() { ... }  // expiry
if delegate_action.sender_id().as_str() != sender_id.as_str() { ... }     // sender match
validate_delegate_action_key(...)?;                   // nonce + access key
``` [5](#0-4) 

None of these checks bind the signature to a specific network. There is no chain ID, genesis hash, or network identifier in the signed payload or in the verification logic.

---

### Impact Explanation

An attacker (malicious relayer, or any party who intercepts the off-chain `SignedDelegateAction`) can:

1. Obtain a `SignedDelegateAction` that Alice created for testnet.
2. Construct a new outer `SignedTransaction` on mainnet (with a valid mainnet `block_hash`, signed by the attacker's own key as relayer).
3. Submit it to mainnet.

If Alice has the same account on mainnet with the same key and the nonce on mainnet is lower than the signed nonce, the action executes on mainnet. The inner actions — token transfers, key additions/deletions, contract deployments, function calls — execute with `predecessor_id = alice` on mainnet, draining mainnet funds or modifying mainnet account state without Alice's consent.

Concrete corrupted values:
- Alice's mainnet token balance decreases by the transferred amount.
- Alice's mainnet access keys are modified (added or deleted) without her authorization.
- A contract is deployed to Alice's mainnet account without her authorization.

---

### Likelihood Explanation

The conditions required are:
1. Alice has the same named account on both testnet and mainnet (extremely common for developers and power users).
2. Alice uses the same key on both networks (common practice during development and testing).
3. The nonce on mainnet is lower than the nonce Alice used on testnet (likely if Alice is more active on testnet).
4. `max_block_height` has not expired on mainnet (depends on the value chosen; many implementations use large values or `BlockHeight::MAX`).

The relayer model explicitly involves a third party receiving the `SignedDelegateAction` off-chain before submitting it. A malicious relayer service, or an attacker who intercepts the payload in transit, can trivially perform the replay. The user has no protocol-level protection against this.

---

### Recommendation

Add a chain-specific binding to the `DelegateAction` signed payload. The standard approach is to include a `chain_id` (or genesis block hash) field directly in `DelegateAction` and `DelegateActionV2`, and assert it against the executing network's chain ID inside `apply_delegate_action`. Alternatively, include the chain ID in the `SignableMessage` discriminant or as a mandatory prefix in the signed bytes. The `ApplyState` already carries `current_protocol_version` and other chain context; the chain ID can be threaded through from genesis config.

The `VersionedDelegateActionPayload` / `DelegateActionV2` path is a natural place to introduce this as a new required field, since it already uses a versioned enum that prevents cross-version signature grafting.

---

### Proof of Concept

```
// Testnet: Alice signs a DelegateAction to transfer 100 NEAR to Bob
let delegate_action = DelegateAction {
    sender_id: "alice.near",       // same account exists on mainnet
    receiver_id: "bob.near",
    actions: vec![Transfer { deposit: 100_NEAR }],
    nonce: 5,                      // mainnet nonce for alice.near is 3 → valid
    max_block_height: 999_999_999, // far future, valid on mainnet too
    public_key: alice_key,         // same key registered on mainnet
};
let sig = alice_key.sign(delegate_action.get_nep461_hash());
// sig is valid on ANY NEAR network — no chain ID in the signed bytes

// Mainnet: malicious relayer wraps it in a fresh outer transaction
let mainnet_tx = SignedTransaction::from_actions(
    relayer_nonce,
    relayer_id,
    "alice.near",          // outer tx receiver = delegate sender
    &relayer_key,
    vec![Action::Delegate(Box::new(SignedDelegateAction {
        delegate_action,   // ← testnet-signed, replayed on mainnet
        signature: sig,
    }))],
    mainnet_block_hash,    // valid mainnet block hash, signed by relayer
);
// apply_delegate_action on mainnet:
//   verify() → passes (sig is over the same bytes, key is the same)
//   block_height check → passes (max_block_height is far future)
//   sender_id check → passes
//   nonce check → passes (mainnet nonce 3 < signed nonce 5)
// Result: 100 NEAR transferred from alice.near on MAINNET without her consent
``` [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** core/primitives/src/signable_message.rs (L18-25)
```rust
const MIN_ON_CHAIN_DISCRIMINANT: u32 = 1 << 30;
const MAX_ON_CHAIN_DISCRIMINANT: u32 = (1 << 31) - 1;
const MIN_OFF_CHAIN_DISCRIMINANT: u32 = 1 << 31;
const MAX_OFF_CHAIN_DISCRIMINANT: u32 = u32::MAX;

// NEPs currently included in the scheme
const NEP_366_META_TRANSACTIONS: u32 = 366;
const NEP_611_GAS_KEYS: u32 = 611;
```

**File:** core/primitives/src/signable_message.rs (L61-65)
```rust
#[derive(BorshSerialize)]
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}
```

**File:** core/primitives/src/signable_message.rs (L221-223)
```rust
            SignableMessageType::DelegateAction => {
                MessageDiscriminant::new_on_chain(NEP_366_META_TRANSACTIONS).unwrap()
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
