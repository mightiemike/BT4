[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L944-950)
```rust
    pub fn check_and_handle_prepare_phase_start(
        clarity_tx: &mut ClarityTx,
        first_block_height: u64,
        pox_constants: &PoxConstants,
        burn_tip_height: u32,
        coinbase_height: u64,
    ) -> Result<Option<SignerCalculation>, ChainstateError> {
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L959-977)
```rust
        if !pox_constants.is_in_prepare_phase(first_block_height, burn_tip_height.into()) {
            // if we're not in a prepare phase, don't need to do anything
            return Ok(None);
        }

        let Some(cycle_of_prepare_phase) =
            pox_constants.reward_cycle_of_prepare_phase(first_block_height, burn_tip_height.into())
        else {
            // if we're not in a prepare phase, don't need to do anything
            return Ok(None);
        };

        // Dispatch must be cycle-stable: every block of this prepare phase
        // must agree on which pox contract supplies cycle_of_prepare_phase's
        // signer set, regardless of which block first triggers the update.
        // Tip-keyed `active_pox_contract` is wrong here -- it can flip
        // mid-prepare-phase if pox_5_activation_height falls inside it.
        let active_pox_contract =
            pox_constants.active_pox_contract_for_cycle(first_block_height, cycle_of_prepare_phase);
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L1011-1022)
```rust
                // if the cycle_number is less than `cycle_of_prepare_phase`, we need to update
                //  the .signers state.
                let needs_update = cycle_number < u128::from(cycle_of_prepare_phase);
                Ok(needs_update)
            });

        let needs_update = needs_update_result?;

        if !needs_update {
            debug!("Current cycle has already been setup in .signers or .signers is not initialized yet");
            return Ok(None);
        }
```
