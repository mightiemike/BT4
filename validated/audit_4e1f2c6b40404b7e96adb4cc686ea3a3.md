### Title
Poison-microblock validation compares raw header bytes instead of canonical consensus content, letting an attacker slash a miner using an ECDSA-malleated signature ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`handle_poison_microblock` and the fork-detection logic in `validate_parent_microblock_stream` treat "two headers are a valid double-sign" as "two headers with different raw bytes/`block_hash()`, same sequence, and the same recovered signer," rather than "two headers with different *canonical* consensus content (`prev_block`, `tx_merkle_root`)". Because ECDSA signatures over the same message are malleable, an attacker who never possesses the miner's private key can derive a second, distinct-but-valid `MessageSignature` for an already-published microblock header, producing a second header that recovers to the *same* pubkey hash, has the *same* sequence/`prev_block`/`tx_merkle_root`, but a different raw byte hash. This header pair satisfies every check inside `handle_poison_microblock` and gets accepted as a genuine equivocation, slashing/redirecting the honest miner's coinbase to the attacker.

### Finding Description
The equality the system is supposed to enforce is:
`valid double-sign ⇔ header_A.canonical_content ≠ header_B.canonical_content (same sequence, same signer)`

but the code actually enforces:
`accepted double-sign ⇔ header_A.raw_bytes ≠ header_B.raw_bytes (same sequence, same recovered signer)`

Evidence:
- `StacksChainState::check_microblock_header_signer` (`stackslib/src/chainstate/stacks/db/transactions.rs:686-713`) only recovers and compares the pubkey hash from each header's signature; it never compares `tx_merkle_root`, `prev_block`, or `version` for content-equality. [1](#0-0) 
- `handle_poison_microblock` (`stackslib/src/chainstate/stacks/db/transactions.rs:722-883`) calls only `check_microblock_header_signer`, then looks up the pubkey-hash height and records the punishment keyed purely on `mblock_header_1.sequence`. There is no assertion that `mblock_header_1` and `mblock_header_2` differ in any consensus-relevant field (only that a *report* for that sequence didn't already exist). [2](#0-1) 
- The fork-detector that originally builds the `PoisonMicroblock` payload from an observed stream, `StacksChainState::validate_parent_microblock_stream`, flags a fork purely by raw `block_hash()` inequality at equal sequence: `prior_microblock.block_hash() != cur_microblock.block_hash()` — not by comparing `tx_merkle_root`/`prev_block` semantics. [3](#0-2) 

Exploit flow: any honest miner's signed microblock header is public once broadcast. `MessageSignature` stores a 65-byte recoverable ECDSA signature `(recovery_id, r, s)`. For any valid `(r, s)` there is a second valid signature `(r, n-s)` with the complementary recovery id that recovers to the *identical* public key over the *identical* message. An attacker (no private key required) computes this malleated signature, builds `header_B` identical to the real `header_A` in `version`, `sequence`, `prev_block`, and `tx_merkle_root`, but with the malleated `signature`. They submit `TransactionPayload::PoisonMicroblock(header_A, header_B)`. `check_microblock_header_signer` recovers the same pubkey hash for both, the sequence/prev_block invariants used at the codec layer are satisfied (both fields match), and `handle_poison_microblock` records the punishment and pays the reporting attacker a commission of the miner's maturing coinbase — even though the miner produced only one semantic microblock and never equivocated.

None of the existing guards catch this: `check_tenure_tx`, `verify_signer_signatures`, `validate_vrf_seed`, `common_validate_against_burnchain`, and the MARF hashing are unrelated to this Clarity-level microblock-signature comparison, and no canonical-content equality check or low-S/canonical-signature enforcement was found in `check_microblock_header_signer` or `handle_poison_microblock`.

### Impact Explanation
This lets an unprivileged attacker redirect a legitimately-earned miner coinbase (via the poison-microblock commission mechanism) to themselves without the miner ever having double-signed any semantically distinct content. This is a reward-mispayment bug scoped to the punished miner's coinbase/commission accounting — matching the "poison or reward mis-payment" High-impact category. It does not, by itself, cause a chain split or MARF root divergence, since the payload is processed deterministically and identically by all nodes; the harm is that an *honest* miner is wrongfully punished and an attacker is wrongfully paid.

### Likelihood Explanation
- Precondition: attacker needs no stake, no BTC spend, and no privileged role — only a broadcast microblock header signed by the targeted miner (public information) and the ability to submit a standard Stacks transaction (`PoisonMicroblock`).
- The malleated signature is derived with pure elliptic-curve arithmetic (negate `s`, flip recovery id) — a well-understood, cheap, deterministic operation, requiring no brute force.
- Repeatable against every miner that ever signs a microblock, as long as the pubkey-hash height is within `MINER_REWARD_MATURITY` (the maturation window is checked, but does not prevent the malleability attack — it only bounds the exploitation window). [4](#0-3) 

### Recommendation
In `check_microblock_header_signer` (or a new helper called before it in `handle_poison_microblock`), require that the two headers be a *canonical* equivocation: same `sequence`, and either differing `prev_block` or differing `tx_merkle_root` — i.e., require an actual difference in the fields that determine microblock *content*, not merely a different raw signature/hash. Additionally, enforce canonical (low-S) signature form when parsing `MessageSignature`, or normalize signatures before recovery/comparison, to eliminate ECDSA malleability as a vector for producing "different" headers over identical content.

### Proof of Concept
Rust integration test plan (to be run against `stackslib/src/chainstate/stacks/db/transactions.rs::process_poison_microblock`-style test harness):
1. Generate a miner keypair `block_privk`; sign a microblock header `header_A` (`version`, `sequence=1`, `prev_block=P`, `tx_merkle_root=M`) with `header_A.sign(&block_privk)`.
2. Parse `header_A.signature` as `(recovery_id, r, s)`; compute `s' = secp256k1_order - s`; set `recovery_id' = 1 - recovery_id`; construct `header_B` identical to `header_A` in `version`/`sequence`/`prev_block`/`tx_merkle_root`, but with `signature = (recovery_id', r, s')`.
3. Assert `header_A.check_recover_pubkey().unwrap() == header_B.check_recover_pubkey().unwrap()` (same signer) while `header_A != header_B` and `header_A.tx_merkle_root == header_B.tx_merkle_root && header_A.prev_block == header_B.prev_block` (identical canonical content).
4. Submit `TransactionPayload::PoisonMicroblock(header_A.clone(), header_B.clone())` signed by an unrelated reporter key through `StacksChainState::process_transaction`.
5. Assert the transaction is **currently accepted** (`.unwrap()` succeeds, `get_poison_microblock_report` returns the reporter) — demonstrating the false-positive equivocation is accepted.
6. After the fix, assert the same transaction is rejected because `canonical_content(header_A) == canonical_content(header_B)` despite differing raw signature bytes.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L686-713)
```rust
    fn check_microblock_header_signer(
        mblock_hdr_1: &StacksMicroblockHeader,
        mblock_hdr_2: &StacksMicroblockHeader,
    ) -> Result<Hash160, Error> {
        let pkh1 = mblock_hdr_1.check_recover_pubkey().map_err(|e| {
            Error::InvalidStacksTransaction(
                format!("Failed to recover public key: {:?}", &e),
                false,
            )
        })?;

        let pkh2 = mblock_hdr_2.check_recover_pubkey().map_err(|e| {
            Error::InvalidStacksTransaction(
                format!("Failed to recover public key: {:?}", &e),
                false,
            )
        })?;

        if pkh1 != pkh2 {
            let msg = format!(
                "Invalid PoisonMicroblock transaction -- signature pubkey hash {} != {}",
                &pkh1, &pkh2
            );
            warn!("{}", &msg);
            return Err(Error::InvalidStacksTransaction(msg, false));
        }
        Ok(pkh1)
    }
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-856)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
            .get_microblock_pubkey_hash_height(&pubkh)?;
        let current_height = env.global_context.database.get_current_block_height();

        // for the microblock public key hash we had to process
        env.add_memory(20)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        // for the block height we had to load
        env.add_memory(4)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        // was the referenced public key hash used anytime in the past
        // MINER_REWARD_MATURITY blocks?
        let mblock_pubk_height = match microblock_height_opt {
            None => {
                // public key has never been seen before
                let msg = format!(
                    "Invalid Stacks transaction: microblock public key hash {} never seen in this fork",
                    &pubkh
                );
                warn!("{}", &msg;
                      "microblock_pubkey_hash" => %pubkh
                );

                return Err(Error::InvalidStacksTransaction(msg, false));
            }
            Some(height) => {
                if height
                    .checked_add(
                        u32::try_from(MINER_REWARD_MATURITY).expect("FATAL: maturity > 2^32"),
                    )
                    .expect("BUG: too many blocks")
                    < current_height
                {
                    let msg = format!(
                        "Invalid Stacks transaction: microblock public key hash from height {} has matured relative to current height {}",
                        height, current_height
                    );
                    warn!("{}", &msg;
                          "microblock_pubkey_hash" => %pubkh
                    );

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }
                height
            }
        };

        // add punishment / commission record, if one does not already exist at lower sequence
        let (reporter_principal, reported_seq) = if let Some((reporter, seq)) = env
            .global_context
            .database
            .get_microblock_poison_report(mblock_pubk_height)?
        {
            // account for report loaded
            env.add_memory(u64::from(TypeSignature::PrincipalType.size().map_err(
                |_| Error::Expects("Failed to get size of PrincipalType".into()),
            )?))
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

            // u128 sequence
            env.add_memory(16)
                .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

            if mblock_header_1.sequence < seq {
                // this sender reports a point lower in the stream where a fork occurred, and is now
                // entitled to a commission of the punished miner's coinbase
                debug!("Sender {} reports a better poison-miroblock record (at {}) for key {} at height {} than {} (at {})", &sender_principal, mblock_header_1.sequence, &pubkh, mblock_pubk_height, &reporter, seq;
                    "sender" => %sender_principal,
                    "microblock_pubkey_hash" => %pubkh
                );
                env.global_context.database.insert_microblock_poison(
                    mblock_pubk_height,
                    &sender_principal,
                    mblock_header_1.sequence,
                )?;
                (sender_principal, mblock_header_1.sequence)
            } else {
                // someone else beat the sender to this report
                debug!("Sender {} reports an equal or worse poison-microblock record (at {}, but already have one for {}); dropping...", &sender_principal, mblock_header_1.sequence, seq;
                    "sender" => %sender_principal,
                    "microblock_pubkey_hash" => %pubkh
                );
                (reporter, seq)
            }
        } else {
            // first-ever report of a fork
            debug!(
                "Sender {} reports a poison-microblock record at seq {} for key {} at height {}",
                &sender_principal, mblock_header_1.sequence, &pubkh, &mblock_pubk_height;
                "sender" => %sender_principal,
                "microblock_pubkey_hash" => %pubkh
            );
            env.global_context.database.insert_microblock_poison(
                mblock_pubk_height,
                &sender_principal,
                mblock_header_1.sequence,
            )?;
            (sender_principal, mblock_header_1.sequence)
        };
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L3020-3037)
```rust
        let mut prior_microblock = first_microblock;
        for (j, cur_microblock) in signed_microblocks.iter().skip(1).enumerate() {
            if prior_microblock.header.sequence == cur_microblock.header.sequence
                && prior_microblock.block_hash() != cur_microblock.block_hash()
            {
                // deliberate microblock fork
                debug!(
                    "Deliberate microblock fork at sequence {}",
                    prior_microblock.header.sequence
                );
                return Some((
                    j, // j := `index in signed_microblocks of cur_microblock - 1`
                    Some(TransactionPayload::PoisonMicroblock(
                        prior_microblock.header.clone(),
                        cur_microblock.header.clone(),
                    )),
                ));
            }
```
