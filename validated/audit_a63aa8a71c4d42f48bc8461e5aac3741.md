### Title
DelegateAction (meta-transaction) signing payload contains no chain ID, enabling cross-chain replay on any network fork — (File: `core/primitives/src/action/delegate.rs`)

---

### Summary

`DelegateAction` (NEP-366 meta transactions) are signed off-chain by a user and submitted on-chain by a relayer. The signed payload is hashed with a protocol-level discriminant (`2^30 + 366`) but contains **no chain ID and no genesis-block hash**. The outer `SignedTransaction` that carries the `DelegateAction` is chain-bound via its `block_hash` field, but the inner `SignedDelegateAction` is signed independently and can be extracted and re-submitted on any chain that shares the same account state — exactly the condition that holds immediately after a network fork. Any unprivileged observer can replay a victim's `SignedDelegateAction` on the forked chain, executing the inner actions (token transfers, function calls, key additions, etc.) without the user's consent.

---

### Finding Description

**Signing payload — no chain identifier**

`DelegateAction` is defined as:

```rust
pub struct DelegateAction {
    pub sender_id: AccountId,
    pub receiver_id: AccountId,
    pub actions: Vec<NonDelegateAction>,
    pub nonce: Nonce,
    pub max_block_height: BlockHeight,   // ← plain integer, not a chain-specific hash
    pub public_key: PublicKey,
    // ← no chain_id, no genesis_hash
}
``` [1](#0-0) 

The hash that is actually signed is produced by:

```rust
pub fn get_nep461_hash(&self) -> CryptoHash {
    let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
    let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
    hash(&bytes)
}
``` [2](#0-1) 

`SignableMessage` prepends a 4-byte discriminant (`MIN_ON_CHAIN_DISCRIMINANT + 366 = 2^30 + 366`):

```rust
const MIN_ON_CHAIN_DISCRIMINANT: u32 = 1 << 30;
const NEP_366_META_TRANSACTIONS: u32 = 366;
``` [3](#0-2) 

This discriminant is a **protocol-level constant**, identical on mainnet, testnet, and every fork. No chain ID, genesis hash, or any other chain-specific byte is mixed into the signed payload.

**Why the outer transaction's `block_hash` does not help**

The outer `SignedTransaction` does bind to a specific chain via `block_hash`:

```rust
pub struct TransactionV0 {
    pub signer_id: AccountId,
    pub public_key: PublicKey,
    pub nonce: Nonce,
    pub receiver_id: AccountId,
    pub block_hash: CryptoHash,   // ← chain-specific
    pub actions: Vec<Action>,
}
``` [4](#0-3) 

However, the `SignedDelegateAction` is a self-contained, independently-signed object nested inside `actions`. An attacker who observes a `SignedDelegateAction` on chain A can extract it and wrap it in a **brand-new** outer transaction on chain B (using chain B's block hash). The inner signature remains valid because it covers only the `DelegateAction` fields, none of which are chain-specific.

**Fork-state identity**

Immediately after a fork both chains share identical account state, access-key sets, and nonces. Therefore:

| Guard | Protects against same-chain replay? | Protects against cross-chain replay? |
|---|---|---|
| `nonce` in `DelegateAction` | Yes — nonce advances after execution | **No** — both chains start with the same nonce |
| `max_block_height` | Yes — expires after a block height | **No** — both chains produce blocks at the same heights |
| `block_hash` in outer tx | N/A | **No** — attacker constructs a fresh outer tx |
| `SignableMessage` discriminant | Yes — prevents type confusion | **No** — same constant on every chain |

---

### Impact Explanation

An attacker who observes a `SignedDelegateAction` on one chain after a fork can replay it on the other chain by wrapping it in a new outer transaction. The inner actions execute with `sender_id` as the predecessor, so the victim's account authorizes:

- **Token transfers** (`TransferAction`, NEP-141 `ft_transfer` calls) — direct fund loss.
- **Key additions / deletions** — account takeover on the forked chain.
- **Arbitrary function calls** — any contract interaction the user intended only for the original chain.

The relayer model makes this worse: `DelegateAction` is explicitly designed so that **any third party** can submit it. The attacker does not need to compromise any key; they only need to copy the already-public `SignedDelegateAction` bytes.

---

### Likelihood Explanation

The attack requires a network fork where both chains share the same genesis state. NEAR has not had a contentious fork to date, which keeps the current probability low. However:

- The `DelegateAction` feature is long-lived and production-deployed.
- Protocol upgrades, emergency patches, or governance disputes could produce a fork at any time.
- Once a fork exists the attack is **trivially executable** by any unprivileged observer with no special tooling.

---

### Recommendation

Include a chain-binding value in the `DelegateAction` signed payload. Two equivalent approaches:

1. **Add `chain_id: String` to `DelegateAction`** (or `DelegateActionV2`) and verify it matches the executing chain's genesis `chain_id` during `apply_delegate_action`.
2. **Mix the genesis block hash into the `SignableMessage` discriminant** so the discriminant is chain-specific rather than a global protocol constant.

Either change makes the signature non-transferable across chains. The fix is analogous to EIP-712's `chainId` domain separator.

---

### Proof of Concept

```
1. Alice signs a DelegateAction on mainnet:
      DelegateAction {
          sender_id:       "alice.near",
          receiver_id:     "token.near",
          actions:         [FunctionCall { method: "ft_transfer", ... deposit: 1000 NEAR }],
          nonce:           42,
          max_block_height: 10_000_000,   // far future
          public_key:      alice_mainnet_key,
      }
   Signed with discriminant 0x4000016E (2^30 + 366) — no chain ID.

2. A fork occurs at block 5_000_000.
   Both chains now have alice.near with nonce 41 and alice_mainnet_key registered.

3. Attacker observes the SignedDelegateAction bytes on mainnet (public mempool / block explorer).

4. Attacker constructs a new outer SignedTransaction on the forked chain:
      SignedTransaction {
          transaction: TransactionV0 {
              signer_id:   "attacker.near",
              receiver_id: "alice.near",
              block_hash:  <forked-chain recent block hash>,  // valid on fork
              actions:     [Action::Delegate(alice_signed_delegate_action)],
              ...
          },
          signature: attacker_sig,
      }

5. Forked chain validates:
   - outer tx block_hash  → valid (forked-chain block)
   - SignedDelegateAction.verify() → hash(discriminant || DelegateAction) matches alice_mainnet_key ✓
   - nonce 42 > current nonce 41 ✓
   - max_block_height 10_000_000 > current height ✓

6. ft_transfer executes on the forked chain, draining Alice's tokens without her consent.
```

The root cause is in `DelegateAction::get_nep461_hash` [5](#0-4)  and `SignableMessage` [6](#0-5) , neither of which incorporates any chain-specific identifier.

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

**File:** core/primitives/src/action/delegate.rs (L349-357)
```rust
    /// Delegate action hash used for NEP-461 signature scheme which tags
    /// different messages before hashing
    ///
    /// For more details, see: [NEP-461](https://github.com/near/NEPs/pull/461)
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

**File:** core/primitives/src/transaction.rs (L33-48)
```rust
pub struct TransactionV0 {
    /// An account on which behalf transaction is signed
    pub signer_id: AccountId,
    /// A public key of the access key which was used to sign an account.
    /// Access key holds permissions for calling certain kinds of actions.
    pub public_key: PublicKey,
    /// Nonce is used to determine order of transaction in the pool.
    /// It increments for a combination of `signer_id` and `public_key`
    pub nonce: Nonce,
    /// Receiver account for this transaction
    pub receiver_id: AccountId,
    /// The hash of the block in the blockchain on top of which the given transaction is valid
    pub block_hash: CryptoHash,
    /// A list of actions to be applied
    pub actions: Vec<Action>,
}
```
