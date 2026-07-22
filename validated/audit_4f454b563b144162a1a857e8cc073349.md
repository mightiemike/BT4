### Title
Protobuf `ValidResourceBounds` Deserialization Silently Downgrades `AllResources` to `L1Gas`, Producing a Divergent Transaction Hash - (`crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary
The protobuf deserializer for `ValidResourceBounds` uses a value-based heuristic to reconstruct the resource-bounds variant. When an `AllResources` V3 transaction carries `l2_gas = 0` and `l1_data_gas = 0`, the deserializer silently produces `ValidResourceBounds::L1Gas` instead of `ValidResourceBounds::AllResources`. Because `get_tip_resource_bounds_hash` hashes a different number of felts depending on the variant (2 for `L1Gas`, 3 for `AllResources`), the transaction hash recomputed after a protobuf round-trip diverges from the hash computed at the gateway. Any component that re-derives the hash from the deserialized `Transaction` object — block-sync hash validation, RPC trace/simulation, or re-execution — will bind the wrong hash to the transaction.

### Finding Description

**Lossy protobuf round-trip in `transaction.rs`**

`TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`