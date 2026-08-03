No vulnerability found for this question.

**Analysis:**

`EpochState::verify` at `types/src/epoch_state.rs:41-50` indeed only checks the epoch number matches and delegates signature checking to `ledger_info.verify_signatures(&self.verifier)` [1](#0-0) . However, this is not a missing cross-check bug — the binding of `self.verifier` to the correct on-chain validator set is enforced structurally at every call site that constructs an `EpochState`, not inside `verify()` itself:

1. **Chain-of-trust construction (`EpochChangeProof::verify`)**: `next_epoch_state()` is only ever trusted for the *next* verification step after the *current* `LedgerInfoWithSignatures` has already passed `verifier_ref.verify(ledger_info_with_sigs)` using the previously-trusted verifier [2](#0-1) . Since `next_epoch_state` is a field of `BlockInfo` embedded inside the `LedgerInfo` struct that gets BCS-serialized and hashed for the BLS aggregate signature, any corruption of that field changes the signed hash — `verify_signatures` would then fail unless the attacker also forges valid quorum signatures from the *actual* epoch's validators [3](#0-2) . That requires controlling validator signing keys, not unprivileged input.

2. **Execution-derived construction (`ensure_next_epoch_state`)**: When an `EpochState` is built directly from execution output (not from a peer-supplied proof), it's computed deterministically from the VM's own write set via `ValidatorSet::fetch_config(&write_set_view)`, which reads the actual on-chain `ValidatorSet` resource that the VM just wrote — not from any attacker-controlled data [4](#0-3) .

3. **`TrustedState`/`SpeculativeStreamState` usage**: These callers also only ever install a new `epoch_state` after the ledger info carrying it has itself been verified against the prior trusted verifier [5](#0-4) , and `SpeculativeStreamState::maybe_update_epoch_state` similarly only swaps in a `next_epoch_state()` extracted from a ledger info that is separately checked via `verify_ledger_info_with_signatures` [6](#0-5) .

So while it's true that `EpochState::verify` performs no independent lookup against a canonical on-chain `ValidatorSet`, the security property the question describes ("forged/corrupted `next_epoch_state()` on an already-accepted `LedgerInfo`") is not achievable via unprivileged input: mutating `next_epoch_state` changes the signed `LedgerInfo` hash, so any forged `EpochState` would fail `verify_signatures` unless the attacker already controls a validator quorum's signing keys — which is out of scope per the review's exclusion of "trusted operator mistakes" / validator-compromise scenarios. There is no code path where unprivileged input alone can inject a fabricated `ValidatorVerifier` that both (a) passes `verify_signatures` and (b) doesn't correspond to a legitimately signed epoch transition.

### Citations

**File:** types/src/epoch_state.rs (L41-50)
```rust
    fn verify(&self, ledger_info: &LedgerInfoWithSignatures) -> anyhow::Result<()> {
        ensure!(
            self.epoch == ledger_info.ledger_info().epoch(),
            "LedgerInfo has unexpected epoch {}, expected {}",
            ledger_info.ledger_info().epoch(),
            self.epoch
        );
        ledger_info.verify_signatures(&self.verifier)?;
        Ok(())
    }
```

**File:** types/src/epoch_change.rs (L106-114)
```rust
            // Try to verify each (epoch -> epoch + 1) jump in the EpochChangeProof.
            verifier_ref.verify(ledger_info_with_sigs)?;
            // While the original verification could've been via waypoints,
            // all the next epoch changes are verified using the (already
            // trusted) validator sets.
            verifier_ref = ledger_info_with_sigs
                .ledger_info()
                .next_epoch_state()
                .ok_or_else(|| format_err!("LedgerInfo doesn't carry a ValidatorSet"))?;
```

**File:** types/src/ledger_info.rs (L305-310)
```rust
    pub fn verify_signatures(
        &self,
        validator: &ValidatorVerifier,
    ) -> ::std::result::Result<(), VerifyError> {
        validator.verify_multi_signatures(self.ledger_info(), &self.signatures)
    }
```

**File:** execution/executor/src/workflow/do_get_execution_output.rs (L551-571)
```rust
    fn ensure_next_epoch_state(to_commit: &TransactionsWithOutput) -> Result<EpochState> {
        let last_write_set = to_commit
            .transaction_outputs
            .last()
            .ok_or_else(|| anyhow!("to_commit is empty."))?
            .write_set();

        let write_set_view = WriteSetStateView {
            write_set: last_write_set,
        };

        let validator_set = ValidatorSet::fetch_config(&write_set_view)?
            .ok_or_else(|| anyhow!("ValidatorSet not touched on epoch change"))?;
        let configuration = ConfigurationResource::fetch_config(&write_set_view)?
            .ok_or_else(|| anyhow!("Configuration resource not touched on epoch change"))?;

        Ok(EpochState::new(
            configuration.epoch(),
            (&validator_set).into(),
        ))
    }
```

**File:** types/src/trusted_state.rs (L161-193)
```rust
        if self.epoch_change_verification_required(latest_li.ledger_info().next_block_epoch()) {
            // Verify the EpochChangeProof to move us into the latest epoch.
            let epoch_change_li = epoch_change_proof.verify(self)?;
            let new_epoch_state = epoch_change_li
                .ledger_info()
                .next_epoch_state()
                .cloned()
                .ok_or_else(|| {
                    format_err!(
                        "A valid EpochChangeProof will never return a non-epoch change ledger info"
                    )
                })?;

            // If the latest ledger info is in the same epoch as the new verifier, verify it and
            // use it as latest state, otherwise fallback to the epoch change ledger info.
            let new_epoch = new_epoch_state.epoch;

            let verified_ledger_info = if epoch_change_li == latest_li {
                latest_li
            } else if latest_li.ledger_info().epoch() == new_epoch {
                new_epoch_state.verify(latest_li)?;
                latest_li
            } else if latest_li.ledger_info().epoch() > new_epoch && epoch_change_proof.more {
                epoch_change_li
            } else {
                bail!("Inconsistent epoch change proof and latest ledger info");
            };
            let new_waypoint = Waypoint::new_any(verified_ledger_info.ledger_info());

            let new_state = TrustedState::EpochState {
                waypoint: new_waypoint,
                epoch_state: new_epoch_state,
            };
```

**File:** state-sync/state-sync-driver/src/utils.rs (L89-110)
```rust
    pub fn maybe_update_epoch_state(
        &mut self,
        ledger_info_with_signatures: LedgerInfoWithSignatures,
    ) {
        if let Some(epoch_state) = ledger_info_with_signatures.ledger_info().next_epoch_state() {
            if ledger_info_with_signatures.ledger_info().version() == self.synced_version {
                self.epoch_state = epoch_state.clone();
            }
        }
    }

    /// Verifies the given ledger info with signatures against the current epoch state
    pub fn verify_ledger_info_with_signatures(
        &mut self,
        ledger_info_with_signatures: &LedgerInfoWithSignatures,
    ) -> Result<(), Error> {
        self.epoch_state
            .verify(ledger_info_with_signatures)
            .map_err(|error| {
                Error::VerificationError(format!("Ledger info failed verification: {:?}", error))
            })
    }
```
