[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** storage/backup/backup-cli/src/backup_types/epoch_ending/restore.rs (L218-240)
```rust
        if let Some(li) = previous_epoch_ending_ledger_info {
            ensure!(
                li.next_block_epoch() == preheat_data.manifest.first_epoch,
                "Previous epoch ending LedgerInfo is not the one expected. \
                My first epoch: {}, previous LedgerInfo next_block_epoch: {}",
                preheat_data.manifest.first_epoch,
                li.next_block_epoch(),
            );
            // Waypoint has been verified in preheat if it's trusted, otherwise try to check
            // the signatures.
            if self
                .controller
                .trusted_waypoints
                .get(&first_li.ledger_info().version())
                .is_none()
            {
                li.next_epoch_state()
                    .ok_or_else(|| {
                        anyhow!("Previous epoch ending LedgerInfo doesn't end an epoch")
                    })?
                    .verify(first_li)?;
            }
        }
```

**File:** types/src/epoch_change.rs (L106-118)
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
        }

        Ok(self.ledger_info_with_sigs.last().unwrap())
    }
```

**File:** types/src/epoch_change.rs (L234-243)
```rust
        // Test non contiguous proof will fail
        let mut list = valid_ledger_info[3..5].to_vec();
        list.extend_from_slice(&valid_ledger_info[8..9]);
        let proof_4 = EpochChangeProof::new(list, /* more = */ false);
        assert!(proof_4
            .verify(&EpochState {
                epoch: all_epoch[3],
                verifier: validator_verifier[3].clone(),
            })
            .is_err());
```
