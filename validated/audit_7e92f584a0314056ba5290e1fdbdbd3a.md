No vulnerability found for this question.

**Analysis:** The exploit hypothesis assumes an attacker can control the bytes that populate `IDX_EK_SENDER` and `IDX_EK_RECIP` in the transfer statement via arbitrary `_A_bytes`/`deserialize_points` input. That is not how the transfer path works.

In `confidential_asset::confidential_transfer`, `ek_sender` and `ek_recip` are **not** derived from attacker-supplied proof bytes — they are read directly from each account's on-chain `ConfidentialStore`: [1](#0-0) 

`get_encryption_key` simply looks up the stored encryption key for the given address: [2](#0-1) 

These stored keys are only set via `register` (initial registration proof) or `rotate_encryption_key` (key-rotation sigma proof), both of which require the caller to prove knowledge of the corresponding decryption key. The `deserialize_points`/`deserialize_compressed_points` helpers in `sigma_protocol_utils.move` are used to parse the *ciphertext point components* (e.g. `new_P`, `new_R`, `amount_P`, `amount_R_sender`, `amount_R_recip`) supplied in the transfer proof — not the encryption keys themselves: [3](#0-2) 

So an attacker cannot make `IDX_EK_SENDER` and `IDX_EK_RECIP` collide by crafting duplicate raw bytes in the transfer payload — those statement slots are populated from `compressed_ek_sender`/`compressed_ek_recip`, which are bound to the `from`/`to` account addresses' registered on-chain keys, not from the proof's serialized point vector: [4](#0-3) 

The only way `ek_sender == ek_recip` could occur is if the sender legitimately transfers to a recipient who happens to use the same registered encryption key (e.g., a self-transfer or two accounts sharing a key by choice). In that case the equations are not "corrupted" — the sigma protocol still requires the prover to know the discrete log `dk` consistent with that key and the correct openings `r_j` for both the `R^sid` and `R^rid` components, per the `psi`/`f` homomorphism check in `sigma_protocol_transfer.move` (lines 391-450, 543-560). Equal keys do not make the witness scalars satisfy two independent, unrelated statements simultaneously; they just mean both check-equations reduce to the same key, which is mathematically consistent and does not allow forging balances for an unrelated recipient. There is no path by which unprivileged input can decouple `ca_store.available_balance` and `recip_ca_store.pending_balance` updates from a valid, sound proof.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/confidential_asset.move (L631-643)
```text
        let effective_auditor = get_effective_auditor_config(asset_type);
        let ek_sender = get_encryption_key(from, asset_type);
        let ek_recip = get_encryption_key(to, asset_type);
        let old_balance = get_available_balance(from, asset_type);

        // Note: Sender's amount in `TransferProof::compressed_amount::compressed_R_sender` is not used here; only included so it can be indexed for dapps that need it
        let (compressed_new_balance, amount, compressed_amount, ek_volun_auds) =
            assert_valid_transfer_proof(
                sender, to, asset_type,
                &ek_sender, &ek_recip,
                &old_balance, &effective_auditor.config.ek,
                proof
            );
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/confidential_asset.move (L1007-1012)
```text
    #[view]
    public fun get_encryption_key(
        user: address, asset_type: Object<fungible_asset::Metadata>
    ): CompressedRistretto acquires ConfidentialStore {
        borrow_confidential_store(user, asset_type).ek
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_utils.move (L43-53)
```text
    public(friend) fun deserialize_points(points_bytes: vector<vector<u8>>): (vector<RistrettoPoint>, vector<CompressedRistretto>) {
        let points = vector[];
        let compressed_points = vector[];
        points_bytes.for_each(|point_bytes| {
            let (point, compressed_point) = new_point_and_compressed_from_bytes(point_bytes);
            points.push_back(point);
            compressed_points.push_back(compressed_point);
        });

        (points, compressed_points)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/proofs/sigma_protocol_transfer.move (L249-257)
```text
    public(friend) fun new_transfer_statement(
        compressed_ek_sender: CompressedRistretto,
        compressed_ek_recip: CompressedRistretto,
        compressed_old_balance: &CompressedBalance<Available>,
        compressed_new_balance: &CompressedBalance<Available>,
        compressed_amount: &CompressedAmount,
        compressed_ek_eff_aud: &Option<CompressedRistretto>,
        compressed_ek_volun_auds: &vector<CompressedRistretto>,
    ): (Statement<Transfer>, Balance<Pending>) {
```
