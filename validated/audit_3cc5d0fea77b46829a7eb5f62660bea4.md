## Title
Signer's local proposal-timing gap forces a stale-timing default that lets a malicious miner make signers disagree on whether a tenure reorg is permitted - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to build a new tenure on top of something other than the immediately-prior sortition (a tenure reorg). The rule is supposed to be: a reorg of a tenure that already mined a block is only permitted if that tenure's first block was proposed pathologically late (within `first_proposal_burn_block_timing` of the next burn block). When a given signer never itself signed the reorged tenure's first block, the code substitutes `proposal_to_sortition = 0` instead of computing the real elapsed time, which always satisfies the "arrived late" test and unconditionally permits the reorg. A miner can exploit signer-to-signer message-delivery variance (no majority collusion needed) to make some signers see the block (and correctly reject the reorg) while others never receive/sign it (and wrongly permit it), producing a supermajority-vs-signer state divergence on which tenure is canonical.

### Finding Description
In `check_parent_tenure_choice` [1](#0-0) , once a signer determines its miner is not building on the immediately-prior sortition, it inspects every reorged tenure in `tenures_reorged`. For any reorged tenure that has already produced a globally-accepted first block, the function is supposed to only allow the reorg if that block's proposal-to-sortition timing was very close to the burn-block transition: [2](#0-1) 

The `proposal_to_sortition` value is computed from `local_block_info.approved_time`, which is populated only if *this* signer itself locally approved (pre-committed to or signed) that tenure's first block. If the signer never received/signed that block — e.g. because a miner (who controls proposal broadcast order and timing) never sent it that proposal, or sent it too late for this signer to process before the next sortition — the code falls into the `else` branch and hard-codes `proposal_to_sortition = 0`. Since `Duration::from_secs(0)` is always `< first_proposal_burn_block_timing` (a strictly positive duration), `checked_proposal_timing` evaluates to `true` unconditionally, and the tenure is added to `superseded_tenures`, i.e. the reorg is treated as permitted — even if the real elapsed time was long and the reorg should have been refused.

Signers that *did* receive and sign the same block will compute the real (non-zero) `proposal_to_sortition` and correctly reject the reorg if it was not actually late. This creates two different verdicts for the identical objective fact ("was tenure X's first block proposed pathologically late") depending purely on a signer's private delivery history, which a byzantine or merely slow/partitioning miner can manipulate by choosing to whom, and when, it sends the tenure's first block proposal. The invariant broken is: "the equality of the objective proposal-to-sortition timing must be the same fact for every honest signer" — instead it silently degrades to `0` for any signer lacking local knowledge, always resolving in the attacker's favor.

### Impact Explanation
This is a minority-triggerable signer/validation divergence: the miner (a single, unprivileged actor with respect to consensus, only controlling its own block-proposal delivery) can cause part of the signer set to accept a tenure reorg that the rest of the signer set (or the honest majority) would reject, or vice versa. This falls under the "High" bucket in the rules: "a minority-triggerable sortition/VRF/static-validation divergence... temporary tip disagreement" — some signers sign/accept the reorging tenure's blocks while others refuse them, producing signer-set disagreement and stalled/forked block production until the state resynchronizes via the stacks node's canonical view. It does not by itself cause irreversible fund loss or a permanent chain split (the stacks-node-side `check_nakamoto_tenure`/`select_winning_block` checks are independent and still enforce actual sortition-derived ancestry), but it can cause the signer set's local acceptance state to diverge, delaying block finalization and creating exploitable inconsistency in which blocks get signed.

### Likelihood Explanation
Triggering the divergence only requires a miner to control which signers receive a given tenure's first block proposal in time — something well within a miner's normal operational control (network timing, selective broadcast, or even accidental partition/latency), and does not require compromising any signer key, obtaining majority weight, or any other privileged capability. The `else => 0` fallback is reached any time a signer's local `SignerDb` lacks an `approved_time` for the reorged tenure's first block, which is an ordinary and not-infrequent condition (new signers joining, transient connectivity gaps, signer restarts, or a miner intentionally delaying delivery to specific signers).

### Recommendation
Do not default `proposal_to_sortition` to `0` (a value that always satisfies the "late" check) when the local signer lacks `approved_time` for the reorged tenure's block. Instead, either: (1) refuse to treat the reorg as automatically permitted in this ambiguous case (mirroring the fail-closed behavior used elsewhere, e.g. `get_tenure_last_block_info`), or (2) query the stacks-node (as is already done via `client.get_tenure_forking_info`) for the tenure's actual first-block-proposal timestamp instead of relying solely on local, potentially-missing state, so that every signer bases its "was it late" decision on the same objective fact rather than on its own incomplete visibility.

### Proof of Concept
1. A miner creates tenure T with a first block B that is *not* pathologically late (i.e., it should not qualify as a permissible reorg target).
2. The miner deliberately withholds/delays delivering the proposal for B to signer S1 (so S1 never sets `approved_time` for B in `SignerDb`), while delivering it normally to signer S2 (which signs and records `approved_time`).
3. The miner mines a competing sortition whose tenure reorgs T, and sends the tenure-change block proposal to both S1 and S2.
4. In `check_parent_tenure_choice`:
   - S2 computes `proposal_to_sortition = sortition_state_received_time - approved_at` (a real, non-trivial duration) and correctly determines the reorg is not late enough → rejects (`Ok(false)`).
   - S1 has `local_block_info.approved_time == None`, hits the `else` branch at [3](#0-2) , sets `proposal_to_sortition = 0`, and unconditionally passes the timing check → accepts the reorg (`Ok(true)`).
5. S1 and S2 now disagree on whether the new tenure is a valid choice, causing divergent signing behavior across the signer set for the same underlying fact.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L170-182)
```rust
    pub fn check_parent_tenure_choice(
        &self,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        first_proposal_burn_block_timing: &Duration,
    ) -> Result<bool, SignerChainstateError> {
        // if the parent tenure is the last sortition, it is a valid choice.
        // if the parent tenure is a reorg, then all of the reorged sortitions
        //  must either have produced zero blocks _or_ produced their first (and only) block
        //  very close to the burn block transition.
        if self.prior_sortition == self.parent_tenure_id {
            return Ok(true);
        }
```

**File:** stacks-signer/src/chainstate/mod.rs (L247-278)
```rust
            let checked_proposal_timing = if let Some(sortition_state_received_time) =
                sortition_state_received_time
            {
                // how long was there between when the proposal was received and the next sortition started?
                let proposal_to_sortition = if let Some(approved_at) =
                    local_block_info.approved_time
                {
                    sortition_state_received_time.saturating_sub(approved_at)
                } else {
                    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
                    0
                };
                if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
                    info!(
                        "Miner is not building off of most recent tenure. A tenure they reorg has already mined blocks, but the block was poorly timed, allowing the reorg.";
                        "parent_tenure" => %self.parent_tenure_id,
                        "last_sortition" => %self.prior_sortition,
                        "violating_tenure_id" => %tenure.consensus_hash,
                        "violating_tenure_first_block_id" => %first_block_mined,
                        "violating_tenure_proposed_time" => local_block_info.proposed_time,
                        "new_tenure_received_time" => sortition_state_received_time,
                        "new_tenure_burn_timestamp" => self.burn_header_timestamp,
                        "first_proposal_burn_block_timing_secs" => first_proposal_burn_block_timing.as_secs(),
                        "proposal_to_sortition" => proposal_to_sortition,
                    );
                    superseded_tenures.push(tenure);
                    continue;
                }
                true
            } else {
                false
            };
```
