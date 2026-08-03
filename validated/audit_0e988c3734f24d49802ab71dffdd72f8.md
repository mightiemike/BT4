No vulnerability found for this question.

**Reasoning:**

The claim rests on a hypothetical restore-equality check that does not exist in the codebase. Investigation shows:

1. `StateValue`'s own `PartialEq` implementation correctly includes metadata in the comparison — `self.data == other.data && self.metadata == other.metadata` [1](#0-0) . This is the type actually used for state-value equality checks in restore/replay/proof paths (e.g., JMR leaf verification, restore reconciliation), and it does account for metadata.

2. `ValidatorPerformance`/`ValidatorPerformances` are plain decoded Move-resource structs with derived `PartialEq`/`Eq` over their fields only [2](#0-1) . There is no restore, replay, or proof-verification code path found that decodes a `ValidatorPerformances` resource and then uses its derived `PartialEq` as a substitute for comparing the raw `StateValue` (including metadata) during a restore-integrity check — a search for such usage (`ValidatorPerformances::`, `bcs::from_bytes` decode/compare patterns, restore verification helpers) turned up no results tying this struct to any restore-equality logic.

3. `PersistedStateValue::V0` vs `WithMetadata` [3](#0-2)  only affects the *serialized wire format* of a `StateValue`; once deserialized into the in-memory `StateValue`, both forms carry an explicit `StateValueMetadata` (with `none()` for legacy `V0`), and the `PartialEq` impl above compares that metadata field, so two `StateValue`s with different metadata are correctly judged unequal regardless of which persisted encoding produced them.

Since no actual code path decodes committed `ValidatorPerformance(s)` data and substitutes struct-level equality for full `StateValue` (data+metadata) equality in a restore/replay/proof-verification flow, this is a speculative construction rather than a demonstrable divergence in production commit, proof, storage, restore, or authenticated-response logic. The premise conflates "a struct has derived `PartialEq` that ignores metadata" (true, but metadata isn't part of that struct's serialized form or its usage context) with "a restore-integrity check uses that `PartialEq` in place of state-value equality" (unsubstantiated).

### Citations

**File:** types/src/state_store/state_value.rs (L161-180)
```rust
#[derive(BCSCryptoHash, CryptoHasher, Deserialize, Serialize)]
#[serde(rename = "StateValue")]
enum PersistedStateValue {
    V0(Bytes),
    WithMetadata {
        data: Bytes,
        metadata: PersistedStateValueMetadata,
    },
}

impl PersistedStateValue {
    fn into_in_mem_form(self) -> StateValue {
        match self {
            PersistedStateValue::V0(data) => StateValue::new_legacy(data),
            PersistedStateValue::WithMetadata { data, metadata } => {
                StateValue::new_with_metadata(data, metadata.into_in_mem_form())
            },
        }
    }
}
```

**File:** types/src/state_store/state_value.rs (L189-201)
```rust
impl PartialEq for StateValue {
    fn eq(&self, other: &Self) -> bool {
        // Fast path: if both have rapid hashes and they differ, values can't be equal
        if let (Some(hash1), Some(hash2)) = (&self.maybe_rapid_hash, &other.maybe_rapid_hash) {
            if hash1 != hash2 {
                return false;
            }
        }

        // Full comparison: data and metadata
        self.data == other.data && self.metadata == other.metadata
    }
}
```

**File:** types/src/validator_performances.rs (L6-15)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ValidatorPerformance {
    pub successful_proposals: u64,
    pub failed_proposals: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ValidatorPerformances {
    pub validators: Vec<ValidatorPerformance>,
}
```
