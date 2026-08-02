[1](#0-0) [2](#0-1)

### Citations

**File:** api/types/src/ledger_info.rs (L25-44)
```rust
    pub fn new(
        chain_id: &ChainId,
        info: &LedgerInfoWithSignatures,
        oldest_ledger_version: u64,
        oldest_block_height: u64,
        block_height: u64,
        txn_encryption_key: Option<String>,
    ) -> Self {
        let ledger_info = info.ledger_info();
        Self {
            chain_id: chain_id.id(),
            epoch: U64::from(ledger_info.epoch()),
            ledger_version: ledger_info.version().into(),
            oldest_ledger_version: oldest_ledger_version.into(),
            block_height: block_height.into(),
            oldest_block_height: oldest_block_height.into(),
            ledger_timestamp: ledger_info.timestamp_usecs().into(),
            txn_encryption_key,
        }
    }
```

**File:** api/src/context.rs (L1-1)
```rust
// Copyright (c) Aptos Foundation
```
