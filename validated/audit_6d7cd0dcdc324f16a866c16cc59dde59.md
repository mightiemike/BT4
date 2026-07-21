### Title
Protobuf `ValidResourceBounds` zero-value heuristic misclassifies `AllResources` transactions as `L1Gas`, producing a divergent hash preimage and wrong `get_execution_info` output - (`crates/apollo_protobuf/src/converters/transaction.rs`)

---

### Summary

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` implementation uses a zero-value heuristic to distinguish between `ValidResourceBounds::L1Gas` (pre-0.13.3) and `ValidResourceBounds::AllResources` (post-0.13.3) transactions. Any `All