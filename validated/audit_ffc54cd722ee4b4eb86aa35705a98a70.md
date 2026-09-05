### Title
Weak fork-identity check in `TransactionPayload::consensus_deserialize` (PoisonMicroblock) accepts non-diverging header pairs - ([File: stacks-codec/src/transaction.rs])

### Summary
The PoisonMicroblock deserialization only requires that the two headers not be byte-identical (`h1 == h2`) and that they share *either* the same `sequence` *or* the same `prev_block` — an OR, not a check that they actually represent divergent content. [1](#0-0)  This means two headers with identical `sequence`, `prev_block`, and `tx_merkle_root` — differing only in a field such as the signature bytes or `version` — will pass the deserialize-time "fork" check, even though they encode the exact same microblock content.

### Finding Description
The broken equality the question describes is: **"two headers proving a genuine equivocation" == "two headers that satisfy `h1 != h2` AND (`h1.sequence == h2.sequence` OR `h1.prev_block == h2.prev_block`)"**. The code at `stacks-codec/src/transaction.rs:2909-2922` implements exactly the right-hand side, and it is strictly weaker than the left-hand side:

```
// must differ in some field
if h1 == h2 { ... error ... }
// must have the same sequence number or same block parent
if h1.sequence != h2.sequence && h1.prev_block != h2.prev_block { ... error ... }
``` [1](#0-0) 

Nothing here inspects `tx_merkle_root`. A pair of headers with identical `sequence`, `prev_block`, and `tx_merkle_root` (i.e., identical committed content) but a different `signature` (or `version`) byte-for-byte will pass both checks, because `h1 == h2` is false (signatures differ) and the sequence/prev_block equality clause is trivially satisfied.

I traced this as far as the codec-level check, which is exactly the code the question targets and is directly confirmed. I was **not able to retrieve and confirm the body of `handle_poison_microblock` / the actual reward-slashing logic in `stackslib/src/chainstate/stacks/db/transactions.rs`**, nor the exact signature-verification routine used for `StacksMicroblockHeader` (i.e., whether it enforces canonical/low-S ECDSA signatures, which would foreclose the "different signature bytes, same message, no private key" malleability path assumed by the question). Without that confirmation I cannot certify that a downstream check does or does not re-validate `tx_merkle_root` equality (which would reject a non-diverging pair as "not a real fork") or reject non-canonical signatures before paying the poison-microblock reward.

### Impact Explanation
If the downstream processing path does not independently require `tx_merkle_root` divergence and does not reject malleable/non-canonical signatures, this would allow a payload representing no real equivocation to be treated as proof of equivocation, resulting in improper slashing/reward re-allocation for a miner who signed only one legitimate microblock — a reward-theft outcome. However, this final step depends on code paths I could not verify in this session (`handle_poison_microblock` internals and the microblock-header signature verification routine), so I cannot confirm end-to-end exploitability.

### Likelihood Explanation
The precondition requiring an attacker (with no private key) to derive a second, byte-distinct, still-valid signature over the identical message is a classic ECDSA malleability property `(r, s) ↔ (r, n-s)` and does not inherently require majority stake — it only requires observing one legitimate broadcast microblock header. Whether it's actually exploitable here depends entirely on whether the signature-verification code enforces canonical (low-S) signatures, which I could not confirm from the code retrieved.

### Recommendation
Regardless of the malleability question, the codec-level check should be tightened to require genuine content divergence, e.g., require `h1.tx_merkle_root != h2.tx_merkle_root` (or equivalently, reject the pair if all consensus-relevant fields other than `signature`/`version` are identical), in addition to the current sequence/prev_block relationship check, at `stacks-codec/src/transaction.rs:2909-2922`.

### Proof of Concept
I could not construct a verifiable, reproducible integration test in this session because I was unable to confirm (a) whether `StacksMicroblockHeader` signature verification enforces canonical low-S signatures (which would block the no-key malleability path), and (b) the exact logic of `handle_poison_microblock` in `stackslib/src/chainstate/stacks/db/transactions.rs`, which may independently check `tx_merkle_root` equality before paying any reward. A conclusive PoC would need to:
1. Construct one legitimate `StacksMicroblockHeader` (seq=N, prev_block=P, tx_merkle_root=R) signed by a test miner key.
2. Attempt to derive a second valid signature for the identical message without the private key (test ECDSA malleability against the actual signature-recovery/verification code used here).
3. Feed both headers into `TransactionPayload::consensus_deserialize` and confirm it accepts them (this part is confirmed by static code reading).
4. Feed the resulting `PoisonMicroblock` transaction through the full mempool-admission and block-processing path (`handle_poison_microblock`) and assert whether a slashing/reward-reassignment result is returned despite `h1.tx_merkle_root == h2.tx_merkle_root`.

Given the inability to confirm steps 2 and 4 with the tools available in this session, I cannot certify this as a fully confirmed, reproducible Critical/High finding per the audit's evidentiary bar — the codec-level gap (step 3) is confirmed and real, but the full exploit chain to reward theft is unverified.

### Citations

**File:** stacks-codec/src/transaction.rs (L2905-2925)
```rust
            TransactionPayloadID::PoisonMicroblock => {
                let h1: StacksMicroblockHeader = read_next(fd)?;
                let h2: StacksMicroblockHeader = read_next(fd)?;

                // must differ in some field
                if h1 == h2 {
                    return Err(codec_error::DeserializeError(
                        "Failed to parse transaction -- microblock headers match".to_string(),
                    ));
                }

                // must have the same sequence number or same block parent
                if h1.sequence != h2.sequence && h1.prev_block != h2.prev_block {
                    return Err(codec_error::DeserializeError(
                        "Failed to parse transaction -- microblock headers do not identify a fork"
                            .to_string(),
                    ));
                }

                TransactionPayload::PoisonMicroblock(h1, h2)
            }
```
