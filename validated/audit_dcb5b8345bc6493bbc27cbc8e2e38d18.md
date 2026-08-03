No vulnerability found for this question.

**Why the premise doesn't hold:**

1. **`Arc<T>`'s `PartialEq` compares values, not pointer identity.** The standard library implements `impl<T: PartialEq> PartialEq for Arc<T>` by delegating to the inner `T::eq` (dereferencing), not `Arc::ptr_eq`. So `EpochState`'s derived `PartialEq` (which includes the `verifier: Arc<ValidatorVerifier>` field) actually compares the underlying `ValidatorVerifier` contents, not Arc pointer addresses. [1](#0-0) 

2. **The `NoChange` check at `trusted_state.rs:215-219` never compares `EpochState` values at all.** In that branch (`epoch_change_verification_required` is false, meaning no epoch transition occurred), `curr_epoch_state` is retained unchanged — it is not replaced by any "new" epoch state derived from `latest_li`. The comparison is strictly `new_waypoint == curr_waypoint`, i.e., a comparison of two `Waypoint` values. [2](#0-1) 

3. **`Waypoint` equality is a cryptographic hash comparison that explicitly includes `next_epoch_state`.** `Waypoint::new_any` hashes `Ledger2WaypointConverter`, which includes `epoch`, `root_hash`, `version`, `timestamp_usecs`, and `next_epoch_state: Option<EpochState>` via `BCSCryptoHash` (BCS-serialize then hash). [3](#0-2) 

Since `EpochState` (including the `Arc<ValidatorVerifier>` contents) is BCS-serialized by value before hashing — not by pointer address — any difference in the underlying validator set/voting power distribution produces a different serialized byte sequence and therefore (barring a cryptographic hash collision on the accumulator/root hash function) a different `Waypoint` value. There is no code path where two structurally different `EpochState`/`ValidatorVerifier` contents serialize identically while genuinely differing, and no code path where `Arc` pointer coincidence (which isn't even how `PartialEq` works here) could substitute for content equality.

Additionally, since the `NoChange` branch never mutates or replaces `curr_epoch_state`, there is no "corrupted" or divergent epoch state being silently retained — the client's verifier stays exactly what it was, correctly, when the waypoint hash matches. The scenario described (two different `LedgerInfo`s at the same version colliding on the `Waypoint` hash while differing in `next_epoch_state`) requires breaking the underlying cryptographic hash function used by `BCSCryptoHash`, which is outside the "unprivileged input" threat model (it's a cryptographic-strength assumption, not a logic bug).

### Citations

**File:** types/src/epoch_state.rs (L17-22)
```rust
#[derive(Clone, Deserialize, Eq, PartialEq, Serialize)]
#[cfg_attr(any(test, feature = "fuzzing"), derive(Arbitrary))]
pub struct EpochState {
    pub epoch: u64,
    pub verifier: Arc<ValidatorVerifier>,
}
```

**File:** types/src/trusted_state.rs (L199-231)
```rust
        } else {
            let (curr_waypoint, curr_epoch_state) = match self {
                Self::EpochWaypoint(_) => {
                    bail!("EpochWaypoint can only verify an epoch change ledger info")
                },
                Self::EpochState {
                    waypoint,
                    epoch_state,
                    ..
                } => (waypoint, epoch_state),
            };

            // The EpochChangeProof is empty, stale, or only gets us into our
            // current epoch. We then try to verify that the latest ledger info
            // is inside this epoch.
            let new_waypoint = Waypoint::new_any(latest_li.ledger_info());
            if new_waypoint.version() == curr_waypoint.version() {
                ensure!(
                    &new_waypoint == curr_waypoint,
                    "LedgerInfo doesn't match verified state"
                );
                Ok(TrustedStateChange::NoChange)
            } else {
                // Verify the target ledger info, which should be inside the current epoch.
                curr_epoch_state.verify(latest_li)?;

                let new_state = Self::EpochState {
                    waypoint: new_waypoint,
                    epoch_state: curr_epoch_state.clone(),
                };

                Ok(TrustedStateChange::Version { new_state })
            }
```

**File:** types/src/waypoint.rs (L126-148)
```rust
/// Keeps the fields of LedgerInfo that are hashed for generating a waypoint.
/// Note that not all the fields of LedgerInfo are included: some consensus-related fields
/// might not be the same for all the participants.
#[derive(Deserialize, Serialize, CryptoHasher, BCSCryptoHash)]
struct Ledger2WaypointConverter {
    epoch: u64,
    root_hash: HashValue,
    version: Version,
    timestamp_usecs: u64,
    next_epoch_state: Option<EpochState>,
}

impl Ledger2WaypointConverter {
    pub fn new(ledger_info: &LedgerInfo) -> Self {
        Self {
            epoch: ledger_info.epoch(),
            root_hash: ledger_info.transaction_accumulator_hash(),
            version: ledger_info.version(),
            timestamp_usecs: ledger_info.timestamp_usecs(),
            next_epoch_state: ledger_info.next_epoch_state().cloned(),
        }
    }
}
```
