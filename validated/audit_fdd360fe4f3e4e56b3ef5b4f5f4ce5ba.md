[1](#0-0) [2](#0-1)

### Citations

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L170-186)
```rust
    pub fn try_add_parent(&self, other: &MinerReward) -> Option<MinerReward> {
        if !other.is_parent() {
            return None;
        }
        if !self.is_child() {
            return None;
        }
        Some(MinerReward {
            address: self.address.clone(),
            recipient: self.recipient.clone(),
            coinbase: self.coinbase,
            tx_fees_anchored: self.tx_fees_anchored,
            tx_fees_streamed_produced: other.tx_fees_streamed_produced,
            tx_fees_streamed_confirmed: self.tx_fees_streamed_confirmed,
            vtxindex: self.vtxindex,
        })
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L428-446)
```rust
    pub fn insert_miner_payment_schedule(
        tx: &mut DBTx,
        block_reward: &MinerPaymentSchedule,
    ) -> Result<(), Error> {
        assert!(block_reward.burnchain_commit_burn < i64::MAX as u64);
        assert!(block_reward.burnchain_sortition_burn < i64::MAX as u64);
        assert!(block_reward.stacks_block_height < i64::MAX as u64);

        let index_block_hash =
            StacksBlockId::new(&block_reward.consensus_hash, &block_reward.block_hash);

        let (payment_type, db_tx_fees_anchored, db_tx_fees_streamed) = match block_reward.tx_fees {
            MinerPaymentTxFees::Epoch2 { anchored, streamed } => {
                (HeaderTypeNames::Epoch2, anchored, streamed)
            }
            MinerPaymentTxFees::Nakamoto { parent_fees } => {
                (HeaderTypeNames::Nakamoto, parent_fees, 0)
            }
        };
```
