### Title
Missing on-chain uniqueness check on `consensus_pubkey` in `stake::rotate_consensus_key`/`initialize_validator` lets an unprivileged validator clone another validator's BLS key, enabling duplicate-key signature "reuse" that breaks `ValidatorVerifier` voting-power attribution - (File: `aptos-move/framework/aptos-framework/sources/stake.move`)

### Summary
`bls12381_pop.rs`'s `ProofOfPossession::verify` only proves that the submitter knows the private key for the exact `pk_bytes` supplied — it binds to nothing else (no chain ID, no account address, no nonce). Because the PoP and the public key are both public values once broadcast (either in the mempool or, more simply, already committed on-chain in another validator's `ValidatorConfig`), any account can resubmit the *identical* `(consensus_pubkey, proof_of_possession)` pair for its own `pool_address` via `stake::rotate_consensus_key` or `stake::initialize_validator`. Neither function checks that the given `consensus_pubkey` is not already registered by a different `ValidatorConfig`, so two independent addresses can end up mapped to the *same* BLS public key in the active `ValidatorSet`. [1](#0-0) 

### Finding Description
`rotate_consensus_key` verifies only that:
1. the caller is the pool's operator, and
2. `new_consensus_pubkey`/`proof_of_possession` form an internally-consistent, non-rogue key pair. [2](#0-1) 

It never checks the new key against other `ValidatorConfig` resources' `consensus_pubkey` values, so a second, entirely unrelated `pool_address` can register that same public key. That the Aptos tooling itself treats duplicate consensus keys as invalid is shown by the genesis CLI validation, which explicitly rejects repeated consensus keys/PoPs across validators — but this check exists only client-side at genesis, not on-chain post-genesis: [3](#0-2) 

Once two active validators (A, legit key holder, and B, attacker-controlled, with its own qualifying stake) share the same BLS public key `P`, `ValidatorVerifier::verify_multi_signatures` breaks the intended 1-signature-per-validator accounting. The aggregation logic treats each bitvec index independently, summing voting power per index and aggregating the corresponding public keys: [4](#0-3) 

If A produces one legitimate BLS signature `S` over a given message (vote/order-vote/commit-vote/`LedgerInfo`) under `P`, that same signature is *also* a valid signature under B's registered key (`P` again, since it's identical). Because `aggregate_signatures` simply sums the individual signature points and mask bits per address with no distinctness requirement on keys: [5](#0-4) 

B can submit `S` as its own partial signature for the same message. The resulting `AggregateSignature` sets both A's and B's bits with `sig = S+S` (curve doubling), and by pairing bilinearity this verifies successfully against `aggregate([P, P]) = 2P`. `check_voting_power` then credits *both* A's and B's voting power for what was cryptographically a single act of signing by A's key: [6](#0-5) 

This directly corrupts the core BFT safety invariant: quorum certificates and `LedgerInfoWithSignatures` are supposed to represent independent voting power from 2f+1 distinct validators. With a cloned key, an entity controlling B (which need not hold any real private key, and never independently signs anything) inflates the effective quorum weight attributable to a single real signature, undermining the guarantee that `verify_multi_signatures`/`verify_signatures` on `LedgerInfoWithSignatures` proves genuine independent quorum agreement: [7](#0-6) 

### Impact Explanation
This is not merely a bookkeeping oddity — it corrupts a proof primitive (`LedgerInfoWithSignatures`/`AggregateSignature`) that the whole system treats as an authenticated attestation of 2f+1 independent validators' agreement. If an attacker can register enough duplicate-key sybil slots (bounded only by the normal minimum-stake requirement to join the active set), a coalition with less real independent voting power than 2f+1 can produce QCs/`LedgerInfo`s that `verify_multi_signatures` accepts as valid, because voting power from duplicate keys is double- (or n-times-) counted from a single underlying signature. This is a hard-fork-only class of divergence: different nodes could accept conflicting certificates as individually "valid" per the verifier's logic, threatening consensus safety and the authenticity of committed `LedgerInfo` state, which downstream (state sync, restore, light-client verification) all trust as ground truth.

### Likelihood Explanation
The path from unprivileged input to impact is straightforward and requires no privileged/trusted-operator mistake: (1) observe any existing validator's already-public `consensus_pubkey`/PoP (on-chain, or in mempool before confirmation), (2) submit `rotate_consensus_key`/`initialize_validator` with the identical bytes for a new/existing pool address that will join the active set with ordinary (permissionless) minimum stake, (3) once active, replay any of A's gossiped partial signatures as B's own vote. No cryptographic secret is needed at any step; PoP verification never binds the key to a specific account/context, so the replay is trivial.

### Recommendation
Bind the PoP (or an additional signed statement) to the submitting `pool_address`/account and chain ID, and/or enforce global uniqueness of `consensus_pubkey` across all `ValidatorConfig` resources on rotation and initialization (reject if the pubkey is already registered to a different pool address), mirroring the check already done off-chain by the genesis CLI tooling.

### Proof of Concept
In an e2e-move-tests harness:
1. Create validator A, call `rotate_consensus_key(A_operator, A_pool, pk_bytes, pop_bytes)` — succeeds per `stake.move` logic at [8](#0-7) .
2. Create validator B (distinct pool address, its own operator/stake), call `rotate_consensus_key(B_operator, B_pool, pk_bytes, pop_bytes)` with the *same* `pk_bytes`/`pop_bytes` copied from step 1 — this also succeeds, since the check is local to the pair and has no cross-pool uniqueness assertion.
3. Assert `borrow_global<ValidatorConfig>(A_pool).consensus_pubkey == borrow_global<ValidatorConfig>(B_pool).consensus_pubkey`.
4. At the crypto layer, construct a `ValidatorVerifier` with both A and B mapped to the same `PublicKey`; sign a message once with the real private key, submit that signature for both A's and B's bitvec slots via `aggregate_signatures`, and confirm `verify_multi_signatures` succeeds while `check_voting_power` counts both A's and B's voting power from the single signature.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L959-990)
```text
    /// Rotate the consensus key of the validator, it'll take effect in next epoch.
    public entry fun rotate_consensus_key(
        operator: &signer,
        pool_address: address,
        new_consensus_pubkey: vector<u8>,
        proof_of_possession: vector<u8>
    ) acquires StakePool, ValidatorConfig {
        assert_reconfig_not_in_progress();
        assert_stake_pool_exists(pool_address);

        let stake_pool = borrow_global_mut<StakePool>(pool_address);
        assert!(
            signer::address_of(operator) == stake_pool.operator_address,
            error::unauthenticated(ENOT_OPERATOR)
        );

        assert!(
            exists<ValidatorConfig>(pool_address),
            error::not_found(EVALIDATOR_CONFIG)
        );
        let validator_info = borrow_global_mut<ValidatorConfig>(pool_address);
        let old_consensus_pubkey = validator_info.consensus_pubkey;
        // Checks the public key has a valid proof-of-possession to prevent rogue-key attacks.
        let pubkey_from_pop =
            &bls12381::public_key_from_bytes_with_pop(
                new_consensus_pubkey,
                &proof_of_possession_from_bytes(proof_of_possession)
            );
        assert!(
            pubkey_from_pop.is_some(), error::invalid_argument(EINVALID_PUBLIC_KEY)
        );
        validator_info.consensus_pubkey = new_consensus_pubkey;
```

**File:** crates/aptos/src/genesis/mod.rs (L754-778)
```rust
            if !unique_consensus_keys
                .insert(validator.consensus_public_key.as_ref().unwrap().clone())
            {
                errors.push(CliError::UnexpectedError(format!(
                    "Validator {} has a repeated a consensus public key {}",
                    name,
                    validator.consensus_public_key.as_ref().unwrap()
                )));
            }

            if validator.proof_of_possession.is_none() {
                errors.push(CliError::UnexpectedError(format!(
                    "Validator {} does not have a consensus proof of possession, though it's joining during genesis",
                    name
                )));
            }
            if !unique_consensus_pops
                .insert(validator.proof_of_possession.as_ref().unwrap().clone())
            {
                errors.push(CliError::UnexpectedError(format!(
                    "Validator {} has a repeated a consensus proof of possessions {}",
                    name,
                    validator.proof_of_possession.as_ref().unwrap()
                )));
            }
```

**File:** types/src/validator_verifier.rs (L320-339)
```rust
    pub fn aggregate_signatures<'a>(
        &self,
        signatures: impl Iterator<Item = (&'a AccountAddress, &'a bls12381::Signature)>,
    ) -> Result<AggregateSignature, VerifyError> {
        let mut sigs = vec![];
        let mut masks = BitVec::with_num_bits(self.len() as u16);
        for (addr, sig) in signatures {
            let index = *self
                .address_to_validator_index
                .get(addr)
                .ok_or(VerifyError::UnknownAuthor)?;
            masks.set(index as u16);
            sigs.push(sig.clone());
        }
        // Perform an optimistic aggregation of the signatures without verification.
        let aggregated_sig = bls12381::Signature::aggregate(sigs)
            .map_err(|_| VerifyError::FailedToAggregateSignature)?;

        Ok(AggregateSignature::new(masks, Some(aggregated_sig)))
    }
```

**File:** types/src/validator_verifier.rs (L349-391)
```rust
    pub fn verify_multi_signatures<T: CryptoHash + Serialize>(
        &self,
        message: &T,
        multi_signature: &AggregateSignature,
    ) -> std::result::Result<(), VerifyError> {
        // Verify the number of signature is not greater than expected.
        Self::check_num_of_voters(self.len() as u16, multi_signature.get_signers_bitvec())?;
        let mut pub_keys = vec![];
        let mut authors = vec![];
        for index in multi_signature.get_signers_bitvec().iter_ones() {
            let validator = self
                .validator_infos
                .get(index)
                .ok_or(VerifyError::UnknownAuthor)?;
            authors.push(validator.address);
            pub_keys.push(validator.public_key());
        }
        // Verify the quorum voting power of the authors
        self.check_voting_power(authors.iter(), true)?;
        #[cfg(any(test, feature = "fuzzing"))]
        {
            if self.quorum_voting_power == 0 {
                // This should happen only in case of tests.
                // TODO(skedia): Clean up the test behaviors to not rely on empty signature
                // verification
                return Ok(());
            }
        }
        // Verify empty multi signature. Decompression of the G2 point is
        // deferred to here, after the cheap structural checks above.
        let multi_sig = multi_signature
            .decompressed_sig()
            .map_err(|_| VerifyError::InvalidMultiSignature)?
            .ok_or(VerifyError::EmptySignature)?;
        // Verify the optimistically aggregated signature.
        let aggregated_key =
            PublicKey::aggregate(pub_keys).map_err(|_| VerifyError::FailedToAggregatePubKey)?;

        multi_sig
            .verify(message, &aggregated_key)
            .map_err(|_| VerifyError::InvalidMultiSignature)?;
        Ok(())
    }
```

**File:** types/src/validator_verifier.rs (L441-454)
```rust
    /// Sum voting power for valid accounts, exiting early for unknown authors
    pub fn sum_voting_power<'a>(
        &self,
        authors: impl Iterator<Item = &'a AccountAddress>,
    ) -> std::result::Result<u128, VerifyError> {
        let mut aggregated_voting_power = 0;
        for account_address in authors {
            match self.get_voting_power(account_address) {
                Some(voting_power) => aggregated_voting_power += voting_power as u128,
                None => return Err(VerifyError::UnknownAuthor),
            }
        }
        Ok(aggregated_voting_power)
    }
```

**File:** types/src/ledger_info.rs (L305-321)
```rust
    pub fn verify_signatures(
        &self,
        validator: &ValidatorVerifier,
    ) -> ::std::result::Result<(), VerifyError> {
        validator.verify_multi_signatures(self.ledger_info(), &self.signatures)
    }

    pub fn check_voting_power(
        &self,
        validator: &ValidatorVerifier,
    ) -> ::std::result::Result<u128, VerifyError> {
        validator.check_voting_power(
            self.get_voters(&validator.get_ordered_account_addresses_iter().collect_vec())
                .iter(),
            true,
        )
    }
```
