## Finding

### Title
Stale `sbtc_address` baked from a one-time `get-current-aggregate-pubkey` snapshot causes PoX-5 Waterfall deposits to be locked to an address the current signer set cannot spend - (File: `stackslib/src/chainstate/nakamoto/signer_set.rs`)

### Summary
`pox_5_compute_and_update_signers` derives the reward cycle's Bitcoin "Waterfall" deposit address once, at the first block of the prepare phase, by reading `get-current-aggregate-pubkey` from the sBTC registry contract at that instant and baking the returned key into a taproot output key that is stored immutably in the `RewardSet` for the whole upcoming cycle. If the aggregate public key is subsequently rotated (a normal, unprivileged sBTC-signer DKG event, not requiring majority collusion) any time after that snapshot but before the address is actually used for the cycle's BTC deposits/payouts, the address permanently baked into consensus state no longer corresponds to a key the current signer set holds shares for.

### Finding Description
The signer-set/reward-set computation snapshots the aggregate key exactly once: [1](#0-0) 

This happens only on the fork's first block of the prepare phase, gated by a `needs_update` check against a stored cycle number in `.signers`: [2](#0-1) 

The resulting `sbtc_address` is written into the `RewardSet::Waterfall` and persisted for the entire next reward cycle, to be used by "the Nakamoto chains coordinator to validate its block-commits and block signatures": [3](#0-2) 

Nothing in this flow re-checks `get-current-aggregate-pubkey` again before the reward cycle actually starts or before Bitcoin deposits/payouts against `sbtc_address` are expected to be honored by the live signer set. The taproot output key is derived purely from the pubkey bytes read at that single snapshot moment: [4](#0-3) 

This is structurally identical to the Intuition finding: a wallet/vault address is computed once from a piece of mutable configuration (`atomWarden` there, the current aggregate pubkey here) and funds are then targeted at that computed address, but the underlying configuration is free to change (via `updateAtomWarden` there, via a normal DKG aggregate-key rotation in the sBTC registry here) before the address is actually "activated"/used, permanently decoupling the computed address from the party that will actually be able to act on it.

### Impact Explanation
BTC sent to the frozen `sbtc_address` for a reward cycle is meant to be redeemable/controllable by the signer set holding the current aggregate private-key shares. If the aggregate key rotates between the prepare-phase snapshot and the point deposits are actually made/consumed during that cycle, funds locked to the stale taproot output become unspendable by the current signer set — an irrecoverable loss of PoX-5 reward/deposit funds. This falls under "reward paid twice or to the wrong party" / permanent freezing of value, since the equality "the address baked into the on-chain reward set == the address the live signer quorum can currently spend from" is broken by a normal signer-set/key-rotation event, not requiring any majority attack — only the routine act of the aggregate key changing after the snapshot.

### Likelihood Explanation
The trigger condition is not a malicious majority attack; it only requires the sBTC registry's `get-current-aggregate-pubkey` to change (e.g. due to routine signer set churn or a scheduled DKG round) at any point after the deterministic prepare-phase-start snapshot but before the corresponding reward cycle's deposits are settled. Since PoX-5/Epoch 4.0 signer sets change every reward cycle and DKG rounds are a normal part of signer operation, this window is realistically reachable without any privileged or coordinated action beyond ordinary signer-set operation.

### Recommendation
Do not bake a single point-in-time aggregate pubkey into the reward set for an entire cycle. Instead, either (a) re-validate/re-derive `sbtc_address` against the current `get-current-aggregate-pubkey` at the actual point of use (block-commit/signature validation and deposit acceptance), rejecting or re-deriving if it has changed since the snapshot, or (b) make the sBTC registry's aggregate-key rotation cycle-aware so that a key approved for cycle `N` cannot be superseded until cycle `N` deposits/payouts have concluded, guaranteeing the snapshot taken in `pox_5_compute_and_update_signers` remains valid for the whole reward cycle it governs.

### Proof of Concept
1. Reach the first block of the prepare phase for reward cycle `N+1`; `check_and_handle_prepare_phase_start` triggers `pox_5_compute_and_update_signers`, which reads `get-current-aggregate-pubkey` (key `K1`) from the sBTC registry and bakes `sbtc_address = taproot(K1)` into `RewardSet::Waterfall` for cycle `N+1`. [1](#0-0) 
2. Before cycle `N+1` begins (or during it, before deposits are consumed), the sBTC signer set legitimately rotates its aggregate key via a DKG round (unrelated to PoX-5's own reward-cycle boundaries), so `get-current-aggregate-pubkey` now returns `K2`.
3. Bitcoin users deposit BTC to `sbtc_address = taproot(K1)` for cycle `N+1` rewards, believing it is controlled by the live signer quorum.
4. The live signer quorum now only holds shares for `K2`, not `K1`; the deposited funds are locked to a taproot output the current signer set cannot spend — permanent loss of the deposited reward funds.

### Citations

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L778-807)
```rust
        let sbtc_registry_contract_id = pox_5_sbtc_registry_contract(is_mainnet);

        let pubkey_buff = clarity
            .eval_method_read_only(
                &sbtc_registry_contract_id,
                "get-current-aggregate-pubkey",
                &[],
            )?
            .expect_buff(33)
            .map_err(|_| {
                ChainstateError::Expects(
                    "get-current-aggregate-pubkey did not return a buffer of <= 33 bytes".into(),
                )
            })?;
        if pubkey_buff.len() != 33 {
            return Err(ChainstateError::Expects(format!(
                    "get-current-aggregate-pubkey returned {} bytes; expected exactly 33 (compressed secp256k1)",
                    pubkey_buff.len()
                )));
        }
        let pubkey_array: [u8; 33] = pubkey_buff.try_into().expect("length checked above");

        let sbtc_recipient = PrincipalData::Contract(boot_code_id(POX_5_NAME, is_mainnet));
        let output_key = sbtc_pox5_deposit_taproot_output_key(
            &pubkey_array,
            &sbtc_recipient,
            POX_5_SBTC_DEPOSIT_MAX_FEE_SATS,
        )?;

        let sbtc_address = PoxAddress::Addr32(is_mainnet, PoxAddressType32::P2TR, output_key);
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L939-943)
```rust
    /// If this block is mined in the prepare phase, based on its tenure's `burn_tip_height`.  If
    /// so, and if we haven't done so yet, then compute the PoX reward set, store it, and update
    /// the .signers contract.  The stored PoX reward set is the reward set for the next reward
    /// cycle, and will be used by the Nakamoto chains coordinator to validate its block-commits
    /// and block signatures.
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L993-1022)
```rust
        // are we the first block in the prepare phase in our fork?
        let needs_update_result: Result<_, ChainstateError> = clarity_tx
            .connection()
            .with_clarity_db_readonly(|clarity_db| {
                if !clarity_db.has_contract(signers_contract) {
                    // if there's no signers contract, no need to update anything.
                    return Ok(false);
                }
                let value = clarity_db.lookup_variable_unknown_descriptor(
                    signers_contract,
                    SIGNERS_UPDATE_STATE,
                    &current_epoch,
                )?;
                let cycle_number = value.expect_u128().map_err(|_| {
                    ChainstateError::Expects(format!(
                        "Expected u128 for .signers {SIGNERS_UPDATE_STATE} variable"
                    ))
                })?;
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

**File:** stackslib/src/chainstate/stacks/sbtc.rs (L88-110)
```rust
/// PoX-5 wrapper around `sbtc_deposit_taproot_output_key`.
///
/// Bakes in the values PoX-5 uses:
/// (`lock_time = u16::MAX`, user-script `[OP_RETURN]`)
///  and accepts the
/// pubkey in 33-byte compressed form (which is what
/// `get-current-aggregate-pubkey` returns).
pub fn sbtc_pox5_deposit_taproot_output_key(
    aggregate_pubkey_compressed: &[u8; 33],
    recipient: &PrincipalData,
    max_fee_sats: u64,
) -> Result<[u8; 32], ChainstateError> {
    let xonly: &[u8; 32] = aggregate_pubkey_compressed[1..]
        .try_into()
        .expect("constant slice length");
    sbtc_deposit_taproot_output_key(
        xonly,
        recipient,
        max_fee_sats,
        u16::MAX,
        &[Opcode::OP_RETURN as u8],
    )
}
```
