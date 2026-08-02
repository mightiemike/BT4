[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [2](#0-1) [5](#0-4) [6](#0-5)

### Citations

**File:** types/src/block_metadata_ext.rs (L42-44)
```rust
/// Frozen wire format: testnet runs with decryption enabled and has committed
/// V2 transactions in this exact layout. Do not change the fields or their
/// encoding; additions go into a new variant (see `V3`).
```

**File:** types/src/block_metadata_ext.rs (L45-74)
```rust
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BlockMetadataWithRandAndDecKey {
    pub id: HashValue,
    pub epoch: u64,
    pub round: u64,
    pub proposer: AccountAddress,
    #[serde(with = "serde_bytes")]
    pub previous_block_votes_bitvec: Vec<u8>,
    pub failed_proposer_indices: Vec<u32>,
    pub timestamp_usecs: u64,
    pub randomness: Option<Randomness>,
    pub decryption_key: Option<BlockTxnDecryptionKey>,
}

/// Replaces `V2` once the dense decryption-round tracking is active (the
/// on-chain `PerBlockDecryptionKeyV2` resource exists): the decryption key is
/// paired with the decryption round it consumed.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BlockMetadataWithRandAndDecPayload {
    pub id: HashValue,
    pub epoch: u64,
    pub round: u64,
    pub proposer: AccountAddress,
    #[serde(with = "serde_bytes")]
    pub previous_block_votes_bitvec: Vec<u8>,
    pub failed_proposer_indices: Vec<u32>,
    pub timestamp_usecs: u64,
    pub randomness: Option<Randomness>,
    pub decryption_payload: Option<DecryptionPayload>,
}
```

**File:** types/src/block_metadata_ext.rs (L147-217)
```rust
    pub fn id(&self) -> HashValue {
        match self {
            BlockMetadataExt::V0(obj) => obj.id(),
            BlockMetadataExt::V1(obj) => obj.id,
            BlockMetadataExt::V2(obj) => obj.id,
            BlockMetadataExt::V3(obj) => obj.id,
        }
    }

    pub fn timestamp_usecs(&self) -> u64 {
        match self {
            BlockMetadataExt::V0(obj) => obj.timestamp_usecs(),
            BlockMetadataExt::V1(obj) => obj.timestamp_usecs,
            BlockMetadataExt::V2(obj) => obj.timestamp_usecs,
            BlockMetadataExt::V3(obj) => obj.timestamp_usecs,
        }
    }

    pub fn proposer(&self) -> AccountAddress {
        match self {
            BlockMetadataExt::V0(obj) => obj.proposer(),
            BlockMetadataExt::V1(obj) => obj.proposer,
            BlockMetadataExt::V2(obj) => obj.proposer,
            BlockMetadataExt::V3(obj) => obj.proposer,
        }
    }

    pub fn previous_block_votes_bitvec(&self) -> &Vec<u8> {
        match self {
            BlockMetadataExt::V0(obj) => obj.previous_block_votes_bitvec(),
            BlockMetadataExt::V1(obj) => &obj.previous_block_votes_bitvec,
            BlockMetadataExt::V2(obj) => &obj.previous_block_votes_bitvec,
            BlockMetadataExt::V3(obj) => &obj.previous_block_votes_bitvec,
        }
    }

    pub fn failed_proposer_indices(&self) -> &Vec<u32> {
        match self {
            BlockMetadataExt::V0(obj) => obj.failed_proposer_indices(),
            BlockMetadataExt::V1(obj) => &obj.failed_proposer_indices,
            BlockMetadataExt::V2(obj) => &obj.failed_proposer_indices,
            BlockMetadataExt::V3(obj) => &obj.failed_proposer_indices,
        }
    }

    pub fn epoch(&self) -> u64 {
        match self {
            BlockMetadataExt::V0(obj) => obj.epoch(),
            BlockMetadataExt::V1(obj) => obj.epoch,
            BlockMetadataExt::V2(obj) => obj.epoch,
            BlockMetadataExt::V3(obj) => obj.epoch,
        }
    }

    pub fn round(&self) -> u64 {
        match self {
            BlockMetadataExt::V0(obj) => obj.round(),
            BlockMetadataExt::V1(obj) => obj.round,
            BlockMetadataExt::V2(obj) => obj.round,
            BlockMetadataExt::V3(obj) => obj.round,
        }
    }

    pub fn type_name(&self) -> &'static str {
        match self {
            BlockMetadataExt::V0(_) => "block_metadata_ext_transaction__v0",
            BlockMetadataExt::V1(_) => "block_metadata_ext_transaction__v1",
            BlockMetadataExt::V2(_) => "block_metadata_ext_transaction__v2",
            BlockMetadataExt::V3(_) => "block_metadata_ext_transaction__v3",
        }
    }
```

**File:** types/src/decryption.rs (L15-20)
```rust
#[derive(Clone, Serialize, Deserialize, Debug, Default, PartialEq, Eq)]
pub struct BlockTxnDecryptionKey {
    metadata: DecKeyMetadata,
    #[serde(with = "serde_bytes")]
    decryption_key: Vec<u8>,
}
```

**File:** types/src/decryption.rs (L97-100)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DecryptionPayload {
    pub key: BlockTxnDecryptionKey,
    pub decryption_round: u64,
```
