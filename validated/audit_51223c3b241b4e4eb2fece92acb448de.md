### Title
Unbounded memory growth in `latest_vote_slot_per_validator` due to missing pruning on stake/root advance - ([File: core/src/cluster_info_vote_listener.rs])

### Summary
The `latest_vote_slot_per_validator: HashMap<Pubkey, Slot>` created in `process_votes_loop` and threaded through `listen_and_confirm_votes` / `filter_and_confirm_with_new_votes` / `track_new_votes_and_notify_confirmations` is only ever inserted into via `.entry(*vote_pubkey).or_insert(0)`, and is never pruned, evicted, or bounded by stake, unlike `VoteTracker` (pruned in `progress_with_new_root_bank`) or `VoteBuffer` (pruned via `prune_stale_slots`). Any unprivileged user who creates a fresh vote account and casts a single vote from it adds a permanent entry to this map for the lifetime of the validator process.

### Finding Description
In `core/src/cluster_info_vote_listener.rs`, `process_votes_loop` initializes `let mut latest_vote_slot_per_validator = HashMap::new();` (line 624) once at thread start and passes it by mutable reference into the per-iteration processing pipeline: `listen_and_confirm_votes` → `filter_and_confirm_with_new_votes` → `track_new_votes_and_notify_confirmations`.

In `track_new_votes_and_notify_confirmations` (line 808 onward), every processed vote transaction results in:
```
let latest_vote_slot = latest_vote_slot_per_validator
    .entry(*vote_pubkey)
    .or_insert(0);
```
(lines 826-828). This entry is keyed purely by `vote_pubkey`, with no relation to current stake, and there is no code path anywhere in the file that calls `.remove()`, `.retain()`, or `.clear()` on `latest_vote_slot_per_validator` (confirmed via search — no matches for removal operations on this map). This is unlike the sibling structures managed in the same loop:
- `vote_tracker.progress_with_new_root_bank(&root_bank)` (line 645) prunes `VoteTracker` state as the root advances.
- `replay_vote_buffer.prune_stale_slots(root_bank.slot())` (line 646) prunes `VoteBuffer` state.

No equivalent pruning exists for `latest_vote_slot_per_validator`. Any pubkey that ever voted — whether or not it currently holds stake, and even after its vote account is closed — remains in the map indefinitely.

An unprivileged attacker can trivially satisfy the precondition: creating a vote account (`InitializeAccount` in the vote program) requires no stake, and submitting one signed vote transaction from that account is enough for it to be parsed by `vote_parser::parse_vote_transaction` and routed into `track_new_votes_and_notify_confirmations` from gossip or replay, inserting a permanent entry. The attacker can then abandon or close the account and repeat with a new vote pubkey, at the cost of only transaction fees and minimal rent, causing the map to grow by one entry per distinct vote pubkey used, with no cap tied to active stake or epoch churn.

### Impact Explanation
This is a long-running memory leak / disproportionate storage cost issue in the validator process (in-memory `HashMap`, not on-chain state): `latest_vote_slot_per_validator` grows monotonically with the cumulative number of distinct vote pubkeys that have ever voted since validator startup, rather than being bounded by current active-stake-weighted authorized voters. Over the life of a long-running validator, an attacker who cheaply churns through many ephemeral vote accounts (one vote transaction each) can force continuous, unbounded heap growth in `process_votes_loop`, degrading memory usage disproportionate to the fees paid. This matches the "unbounded background state / cleanup work not proportional to fees paid" bounty category for disproportionate resource cost.

### Likelihood Explanation
Fully feasible and repeatable by any unprivileged user: creating a vote account and submitting a vote transaction from it requires no stake, no validator/leader/peer control, and no special privileges — only normal transaction fees and vote-account rent. The attacker can automate creation of N distinct vote accounts across epochs, cast one vote transaction from each, and abandon them, with the map size increasing by exactly one entry per unique vote pubkey used, independent of whether that pubkey ever held or retains stake.

### Recommendation
Prune `latest_vote_slot_per_validator` in step with root advancement, analogous to `vote_tracker.progress_with_new_root_bank` and `replay_vote_buffer.prune_stale_slots`. For example, on each root-processing tick in `process_votes_loop`, retain only entries whose `vote_pubkey` is present in the current epoch's (or a recent window of epochs') `epoch_stakes().stakes().vote_accounts()`, removing entries for pubkeys with no delegated stake / not in the active authorized-voter set. Alternatively, cap the map size with an LRU/expiry policy keyed on last-vote-slot recency relative to root.

### Proof of Concept
Integration/unit test plan (Rust, added to `core/src/cluster_info_vote_listener.rs` tests module):

```rust
#[test]
fn test_latest_vote_slot_per_validator_unbounded_growth() {
    // Setup a bank/vote_tracker/subscriptions as in `setup()`.
    let SetupComponents { vote_tracker, bank, subscriptions, .. } = setup();
    let (votes_txs_sender, votes_txs_receiver) = bounded(1024);
    let (_replay_votes_sender, replay_votes_receiver) = bounded(1024);
    let (gossip_verified_vote_hash_sender, _r1) = bounded(1024);
    let (verified_voter_slots_sender, _r2) = bounded(1024);
    let notifiers = ConfirmationNotifiers {
        gossip_verified_vote_hash_sender,
        verified_voter_slots_sender,
        rpc_subscriptions: Some(subscriptions.clone()),
        bank_notification_sender: None,
        duplicate_confirmed_slot_sender: None,
        migration_status: Arc::new(MigrationStatus::default()),
    };
    let mut latest_vote_slot_per_validator = HashMap::new();
    let mut replay_vote_buffer = VoteBuffer::new();

    const N: usize = 10_000; // simulate churn of many ephemeral, unstaked vote accounts
    for _ in 0..N {
        // Create a brand-new, unstaked vote keypair/account and cast exactly one vote.
        let ephemeral = ValidatorVoteKeypairs::new_rand();
        let tower_sync = TowerSync::new_from_slots(vec![1], Hash::default(), None);
        let vote_tx = vote_transaction::new_tower_sync_transaction(
            tower_sync,
            Hash::default(),
            &ephemeral.node_keypair,
            &ephemeral.vote_keypair,
            &ephemeral.vote_keypair,
            None,
        );
        votes_txs_sender.send(vec![vote_tx]).unwrap();

        ClusterInfoVoteListener::listen_and_confirm_votes(
            &votes_txs_receiver,
            &vote_tracker,
            &bank,
            &replay_votes_receiver,
            &mut replay_vote_buffer,
            &notifiers,
            &mut None,
            &mut latest_vote_slot_per_validator,
        )
        .unwrap();
    }

    // Assert: map size grows with N (unbounded, historical), not with actual
    // active-stake-weighted authorized voters (which is 0 for all ephemeral accounts,
    // since none of them are in `bank.epoch_stakes()`).
    assert_eq!(latest_vote_slot_per_validator.len(), N);
    // Expected/fixed behavior would bound this by stake-weighted voter count, e.g. 0
    // for these unstaked ephemeral accounts once pruning is applied on root advance.
}
```
Expected result today: `latest_vote_slot_per_validator.len() == N`, demonstrating unbounded, stake-independent growth proportional to attacker-controlled account churn rather than active validator count. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/src/cluster_info_vote_listener.rs (L613-648)
```rust
    fn process_votes_loop(
        exit: Arc<AtomicBool>,
        gossip_vote_txs_receiver: VerifiedVoteTransactionsReceiver,
        vote_tracker: Arc<VoteTracker>,
        sharable_banks: SharableBanks,
        replay_votes_receiver: ReplayVoteReceiver,
        blockstore: Arc<Blockstore>,
        notifiers: ConfirmationNotifiers,
    ) -> Result<()> {
        let mut confirmation_verifier =
            OptimisticConfirmationVerifier::new(sharable_banks.root().slot());
        let mut latest_vote_slot_per_validator = HashMap::new();
        let mut last_process_root = Instant::now();
        let mut vote_processing_time = Some(VoteProcessingTiming::default());
        let mut replay_vote_buffer = VoteBuffer::new();
        loop {
            if exit.load(Ordering::Relaxed) {
                return Ok(());
            }

            let root_bank = sharable_banks.root();
            if last_process_root.elapsed().as_nanos() > root_bank.ns_per_slot {
                let unrooted_optimistic_slots = confirmation_verifier
                    .verify_for_unrooted_optimistic_slots(&root_bank, &blockstore);
                // SlotVoteTracker's for all `slots` in `unrooted_optimistic_slots`
                // should still be available because we haven't purged in
                // `progress_with_new_root_bank()` yet, which is called below
                OptimisticConfirmationVerifier::log_unrooted_optimistic_slots(
                    &root_bank,
                    &vote_tracker,
                    &unrooted_optimistic_slots,
                );
                vote_tracker.progress_with_new_root_bank(&root_bank);
                replay_vote_buffer.prune_stale_slots(root_bank.slot());
                last_process_root = Instant::now();
            }
```

**File:** core/src/cluster_info_vote_listener.rs (L807-828)
```rust
    #[allow(clippy::too_many_arguments)]
    fn track_new_votes_and_notify_confirmations(
        vote: VoteTransaction,
        vote_pubkey: &Pubkey,
        vote_transaction_signature: Signature,
        vote_tracker: &VoteTracker,
        root_bank: &Bank,
        notifiers: &ConfirmationNotifiers,
        diff: &mut HashMap<Slot, HashMap<Pubkey, bool>>,
        new_optimistic_confirmed_slots: &mut ThresholdConfirmedSlots,
        is_gossip_vote: bool,
        latest_vote_slot_per_validator: &mut HashMap<Pubkey, Slot>,
    ) {
        if vote.is_empty() {
            return;
        }

        let (last_vote_slot, last_vote_hash) = vote.last_voted_slot_hash().unwrap();

        let latest_vote_slot = latest_vote_slot_per_validator
            .entry(*vote_pubkey)
            .or_insert(0);
```

**File:** core/src/cluster_info_vote_listener.rs (L899-931)
```rust
    fn filter_and_confirm_with_new_votes(
        vote_tracker: &VoteTracker,
        gossip_vote_txs: Vec<Transaction>,
        replayed_votes: Vec<ParsedVote>,
        root_bank: &Bank,
        notifiers: &ConfirmationNotifiers,
        vote_processing_time: &mut Option<VoteProcessingTiming>,
        latest_vote_slot_per_validator: &mut HashMap<Pubkey, Slot>,
    ) -> ThresholdConfirmedSlots {
        let mut diff: HashMap<Slot, HashMap<Pubkey, bool>> = HashMap::new();
        let mut new_optimistic_confirmed_slots = vec![];

        // Process votes from gossip and ReplayStage
        let mut gossip_vote_txn_processing_time = Measure::start("gossip_vote_processing_time");
        let votes = gossip_vote_txs
            .iter()
            .filter_map(vote_parser::parse_vote_transaction)
            .zip(repeat(/*is_gossip:*/ true))
            .chain(replayed_votes.into_iter().zip(repeat(/*is_gossip:*/ false)));
        for ((vote_pubkey, vote, _switch_proof, signature), is_gossip) in votes {
            Self::track_new_votes_and_notify_confirmations(
                vote,
                &vote_pubkey,
                signature,
                vote_tracker,
                root_bank,
                notifiers,
                &mut diff,
                &mut new_optimistic_confirmed_slots,
                is_gossip,
                latest_vote_slot_per_validator,
            );
        }
```
