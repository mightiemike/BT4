[1](#0-0) [2](#0-1)

### Citations

**File:** types/src/decryption.rs (L66-73)
```rust
#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct OnchainPerBlockDecryptionKeyV2 {
    pub epoch: u64,
    pub block_round: u64,
    pub decryption_key: Option<Vec<u8>>,
    pub decryption_round: Option<u64>,
    pub next_decryption_round: u64,
}
```

**File:** types/src/decryption.rs (L75-78)
```rust
impl OnChainConfig for OnchainPerBlockDecryptionKeyV2 {
    const MODULE_IDENTIFIER: &'static str = "decryption";
    const TYPE_IDENTIFIER: &'static str = "PerBlockDecryptionKeyV2";
}
```
