The claim fails on multiple independent grounds. Let me trace each one.

**1. `read_int` is test-only code**

The function `read_int` is defined inside the `#[cfg(test)]` block: [1](#0-0) [2](#0-1) 

It is a local test helper that calls `TrieKey::DelayedReceipt { index: i }` and is never compiled into production binaries. No transaction or contract call can reach it.

**2. `TrieKey` encoding is injective by construction**

Every `TrieKey` variant begins with a unique column byte prefix: [3](#0-2) 

Different key types cannot alias because they start with distinct bytes (0–23, with no reuse except the intentional `DELAYED_RECEIPT_OR_INDICES` / `DelayedReceiptIndices` pair, which is safe by length).

**3. The `ACCOUNT_DATA_SEPARATOR` injection path is

### Citations

**File:** chain/chain/src/runtime/trie_update_wrapper.rs (L135-136)
```rust
#[cfg(test)]
mod tests {
```

**File:** chain/chain/src/runtime/trie_update_wrapper.rs (L190-194)
```rust
    fn read_int(t: &impl TrieAccess, i: u64) -> Option<u64> {
        t.get(&int_key(i), AccessOptions::DEFAULT)
            .unwrap()
            .map(|val_bytes| u64::from_be_bytes(val_bytes.try_into().unwrap()))
    }
```

**File:** core/primitives/src/trie_key.rs (L21-86)
```rust
pub mod col {
    /// This column id is used when storing `primitives::account::Account` type about a given
    /// `account_id`.
    pub const ACCOUNT: u8 = 0;
    /// This column id is used when storing contract blob for a given `account_id`.
    pub const CONTRACT_CODE: u8 = 1;
    /// This column id is used when storing `primitives::account::AccessKey` type for a given
    /// `account_id`.
    pub const ACCESS_KEY: u8 = 2;
    /// This column id is used when storing `primitives::receipt::ReceivedData` type (data received
    /// for a key `data_id`). The required postponed receipt might be still not received or requires
    /// more pending input data.
    pub const RECEIVED_DATA: u8 = 3;
    /// This column id is used when storing `primitives::hash::CryptoHash` (ReceiptId) type. The
    /// ReceivedData is not available and is needed for the postponed receipt to execute.
    pub const POSTPONED_RECEIPT_ID: u8 = 4;
    /// This column id is used when storing the number of missing data inputs that are still not
    /// available for a key `receipt_id`.
    pub const PENDING_DATA_COUNT: u8 = 5;
    /// This column id is used when storing the postponed receipts (`primitives::receipt::Receipt`).
    pub const POSTPONED_RECEIPT: u8 = 6;
    /// This column id is used when storing:
    /// * the indices of the delayed receipts queue (a singleton per shard)
    /// * the delayed receipts themselves
    /// The identifier is shared between two different key types for historical reasons. It
    /// is valid because the length of `TrieKey::DelayedReceipt` is always greater than
    /// `TrieKey::DelayedReceiptIndices` when serialized to bytes.
    pub const DELAYED_RECEIPT_OR_INDICES: u8 = 7;
    /// This column id is used when storing Key-Value data from a contract on an `account_id`.
    pub const CONTRACT_DATA: u8 = 9;
    /// This column id is used when storing the indices of the PromiseYield timeout queue
    pub const PROMISE_YIELD_INDICES: u8 = 10;
    /// This column id is used when storing the PromiseYield timeouts
    pub const PROMISE_YIELD_TIMEOUT: u8 = 11;
    /// This column id is used when storing the postponed PromiseYield receipts
    /// (`primitives::receipt::Receipt`).
    pub const PROMISE_YIELD_RECEIPT: u8 = 12;
    /// Indices of outgoing receipts. A singleton per shard.
    /// (`primitives::receipt::BufferedReceiptIndices`)
    pub const BUFFERED_RECEIPT_INDICES: u8 = 13;
    /// Outgoing receipts that need to be buffered due to congestion +
    /// backpressure on the receiving shard.
    /// (`primitives::receipt::Receipt`).
    pub const BUFFERED_RECEIPT: u8 = 14;
    pub const BANDWIDTH_SCHEDULER_STATE: u8 = 15;
    /// Stores `ReceiptGroupsQueueData` for the receipt groups queue
    /// which corresponds to the buffered receipts to `receiver_shard`.
    pub const BUFFERED_RECEIPT_GROUPS_QUEUE_DATA: u8 = 16;
    /// A single item of `ReceiptGroupsQueue`. Values are of type `ReceiptGroup`.
    pub const BUFFERED_RECEIPT_GROUPS_QUEUE_ITEM: u8 = 17;
    /// Global contract code instance. Values are contract blobs,
    /// the same as for `CONTRACT_CODE`.
    pub const GLOBAL_CONTRACT_CODE: u8 = 18;
    /// Global contract deployment nonce. Values are u64.
    pub const GLOBAL_CONTRACT_NONCE: u8 = 19;
    /// Status of a yielded receipt. Values are of type `PromiseYieldStatus`.
    pub const PROMISE_YIELD_STATUS: u8 = 20;
    // Reserved: byte 21 is the `TrieKey::GasKeyNonce` enum discriminant.
    // GasKeyNonce rows live on disk under `ACCESS_KEY` (extending the access-key
    // trie key with a `NonceIndex` suffix), so no `col` constant exists for byte
    // 21. Do not introduce one without coordinating with the `TrieKey` repr.
    /// Mapping from user-provided yield ID to runtime data ID.
    pub const YIELD_ID_TO_DATA_ID: u8 = 22;
    /// Reverse mapping from runtime data ID to user-provided yield ID.
    pub const DATA_ID_TO_YIELD_ID: u8 = 23;

```
