[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/primitives/src/trie_key.rs (L21-30)
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
```

**File:** core/primitives/src/trie_key.rs (L392-394)
```rust
            TrieKey::DelayedReceipt { .. } => {
                col::DELAYED_RECEIPT_OR_INDICES.len() + size_of::<u64>()
            }
```

**File:** core/primitives/src/trie_key.rs (L412-416)
```rust
            TrieKey::BufferedReceipt { index, .. } => {
                col::BUFFERED_RECEIPT.len()
                    + std::mem::size_of::<u16>()
                    + std::mem::size_of_val(index)
            }
```

**File:** core/primitives/src/trie_key.rs (L469-474)
```rust
            TrieKey::AccessKey { account_id, key_handle } => {
                buf.push(col::ACCESS_KEY);
                buf.extend(account_id.as_bytes());
                buf.push(ACCESS_KEY_SEPARATOR);
                append_key_handle_trie_id(buf, key_handle);
            }
```

**File:** core/primitives/src/trie_key.rs (L502-505)
```rust
            TrieKey::DelayedReceipt { index } => {
                buf.push(col::DELAYED_RECEIPT_OR_INDICES);
                buf.extend(&index.to_le_bytes());
            }
```

**File:** core/primitives/src/trie_key.rs (L526-535)
```rust
            TrieKey::BufferedReceipt { index, receiving_shard } => {
                let receiving_shard = *receiving_shard;
                buf.push(col::BUFFERED_RECEIPT);
                // Use  u16 for shard id to reduce depth in trie.
                let receiving_shard: u64 = receiving_shard.into();
                assert!(receiving_shard <= u16::MAX as u64, "Shard ID too big.");
                let receiving_shard: u16 = receiving_shard as u16;
                buf.extend(&receiving_shard.to_le_bytes());
                buf.extend(&index.to_le_bytes());
            }
```

**File:** core/primitives/src/trie_key.rs (L550-556)
```rust
            TrieKey::GasKeyNonce { account_id, key_handle, index: nonce_index } => {
                buf.push(col::ACCESS_KEY);
                buf.extend(account_id.as_bytes());
                buf.push(ACCESS_KEY_SEPARATOR);
                append_key_handle_trie_id(buf, key_handle);
                buf.extend(&nonce_index.to_le_bytes());
            }
```
