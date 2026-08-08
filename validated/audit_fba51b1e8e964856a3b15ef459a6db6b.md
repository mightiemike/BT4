### Title
Unbounded `optimistic_votes_tracker` HashMap growth via Sybil vote-account pubkeys bypassing per-pubkey hash cap - ([File: core/src/cluster_info_vote_listener.rs])

### Summary
`SlotVoteTracker::add_optimistic_vote` caps distinct TowerSync bank hashes to `MAX_VOTE_HASHES_PER_PUBKEY_PER_SLOT` (2) per individual voting pubkey, but this cap is tracked in a per-pubkey counter (`num_optimistic_vote_hashes`) with no global limit on the number of distinct hash keys inserted into `optimistic_votes_tracker` across many different pubkeys. An attacker controlling many distinct vote-account pubkeys (permissionless to create) can each contribute up to 2 unique hashes for the same slot, causing the shared `optimistic_votes_tracker: HashMap<Hash, VoteStakeTracker>` to grow proportionally to the number of Sybil pubkeys rather than the number of honest, distinct bank hashes.

### Finding Description
`SlotVoteTracker` maintains, per slot, an `optimistic_votes_tracker: HashMap<Hash, VoteStakeTracker>` and a `num_optimistic_vote_hashes: HashMap<Pubkey, u8>` counter [1](#0-0) . In `add_optimistic_vote`, the per-pubkey counter is checked against `MAX_VOTE_HASHES_PER_PUBKEY_PER_SLOT = 2` before an entry is inserted or updated in `optimistic_votes_tracker` for a given `hash` [2](#0-1) . This guard only bounds how many *distinct hashes a single pubkey* may contribute — it does nothing to bound the total number of distinct hash keys that accumulate in the shared `optimistic_votes_tracker` map when the votes originate from many different pubkeys. Because vote accounts are permissionlessly creatable by any unprivileged user (via the vote program, paid for by the attacker), an attacker can create N distinct vote-account pubkeys and, for each, submit a vote transaction with a unique/incorrect TowerSync bank hash for a targeted slot. Each such pubkey is allowed to insert up to 2 new hash entries before being capped, so total distinct hash entries in `optimistic_votes_tracker` for that slot can grow to `O(N)`, unconstrained by the per-pubkey cap, entirely driven by Sybil-generated pubkeys rather than legitimate/distinct hashes seen from the honest network.

### Impact Explanation
This causes unbounded (Sybil-scalable) memory growth of the per-slot `optimistic_votes_tracker` HashMap, and each insertion also runs threshold-checking logic (`add_vote_pubkey` against `THRESHOLDS_TO_CHECK`), so CPU cost scales with the number of Sybil-inserted hash entries as well. This matches the "disproportionate storage and CPU cost" impact category — it is a resource-exhaustion vector against the optimistic-confirmation bookkeeping path, potentially degrading or stalling confirmation processing for the affected slot(s) if enough distinct pubkeys/hashes are injected.

### Likelihood Explanation
Feasibility depends on the cost of creating many vote-account pubkeys and getting their vote transactions accepted into the vote-processing pipeline (each requires a signed vote transaction and transaction fees, but does not require delegated stake or validator/leader privileges). This is within the "unprivileged attacker" threat model (create accounts they pay for, control account keys). The attack is repeatable across slots since `SlotVoteTracker` state is maintained per slot and the described bypass applies to any slot the attacker targets. Practical severity depends on rate limits and fee costs elsewhere in the vote/transaction-processing pipeline (e.g., sigverify, gossip stage vote filtering, fees) that were not fully traced in this analysis and may reduce the attacker's practical throughput of unique pubkeys/votes per unit time — this remains a source of uncertainty not fully resolved by the available code context.

### Recommendation
Bound `optimistic_votes_tracker` growth globally per slot (e.g., cap total distinct hash entries per slot, or require minimum aggregate stake before creating a new hash entry, or evict/ignore hash entries backed only by zero/negligible-stake pubkeys) rather than relying solely on a per-pubkey counter that does not constrain multi-pubkey Sybil behavior.

### Proof of Concept
Rust unit/fuzz test plan for `SlotVoteTracker::add_optimistic_vote` (in `core/src/cluster_info_vote_listener.rs`):
```rust
#[test]
fn test_sybil_optimistic_votes_tracker_growth() {
    let mut tracker = SlotVoteTracker::default();
    let total_epoch_stake = 1_000_000;
    // Simulate N distinct Sybil pubkeys, each with 0 or negligible stake,
    // each submitting up to MAX_VOTE_HASHES_PER_PUBKEY_PER_SLOT distinct hashes.
    let n_pubkeys = 10_000;
    for _ in 0..n_pubkeys {
        let pubkey = Pubkey::new_unique();
        for _ in 0..2 {
            let hash = Hash::new_unique();
            tracker.add_optimistic_vote(hash, pubkey, 0, total_epoch_stake);
        }
    }
    // Assert: without a fix, optimistic_votes_tracker grows to ~2 * n_pubkeys
    // distinct entries, demonstrating unbounded growth driven by Sybil pubkeys
    // rather than distinct honest bank hashes.
    assert!(tracker.optimistic_votes_tracker.len() <= EXPECTED_BOUND,
        "optimistic_votes_tracker grew unbounded via Sybil pubkeys: {}",
        tracker.optimistic_votes_tracker.len());
}
```
Expected result on the current implementation: the assertion fails because `optimistic_votes_tracker.len()` scales linearly with `n_pubkeys` (up to `2 * n_pubkeys`), confirming the per-pubkey cap does not bound total map size.

### Citations

**File:** core/src/cluster_info_vote_listener.rs (L81-91)
```rust
#[derive(Default)]
pub struct SlotVoteTracker {
    // Maps pubkeys that have voted for this slot
    // to whether or not we've seen the vote on gossip.
    // True if seen on gossip, false if only seen in replay.
    voted: HashMap<Pubkey, bool>,
    optimistic_votes_tracker: HashMap<Hash, VoteStakeTracker>,
    num_optimistic_vote_hashes: HashMap<Pubkey, u8>,
    voted_slot_updates: Option<Vec<Pubkey>>,
    gossip_only_stake: u64,
}
```

**File:** core/src/cluster_info_vote_listener.rs (L98-118)
```rust
    fn add_optimistic_vote(
        &mut self,
        hash: Hash,
        pubkey: Pubkey,
        stake: u64,
        total_epoch_stake: u64,
    ) -> (Vec<bool>, bool) {
        let num_vote_hashes = self.num_optimistic_vote_hashes.entry(pubkey).or_default();
        if *num_vote_hashes >= MAX_VOTE_HASHES_PER_PUBKEY_PER_SLOT {
            return (vec![false; THRESHOLDS_TO_CHECK.len()], false);
        }

        let result @ (_, is_new) = self
            .optimistic_votes_tracker
            .entry(hash)
            .or_default()
            .add_vote_pubkey(pubkey, stake, total_epoch_stake, &THRESHOLDS_TO_CHECK);

        if is_new {
            *num_vote_hashes += 1;
        }
```
