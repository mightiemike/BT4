### Title
Integer-division rounding in `streamed_tx_fees_confirmed`/`streamed_tx_fees_produced` destroys up to 4 micro-STX of streamed fees per epoch2 block - (File: `stackslib/src/chainstate/stacks/db/accounts.rs`)

### Summary
`MinerPaymentSchedule::streamed_tx_fees_confirmed` computes `(tx_fees_streamed * 3) / 5` and `MinerPaymentSchedule::streamed_tx_fees_produced` computes `(tx_fees_streamed * 2) / 5` independently, each truncating via integer division. For values of `tx_fees_streamed` not divisible by 5, the two truncated shares sum to strictly less than `tx_fees_streamed`, so the remainder is neither paid to the confirming miner nor the producing miner — it is simply never credited (destroyed/unclaimed), not stolen or double-paid.

### Finding Description
The claimed equality `streamed_tx_fees_confirmed() + streamed_tx_fees_produced() == tx_fees_streamed` does not hold for all u128 inputs. [1](#0-0) 

For example, with `tx_fees_streamed = 3`: `(3*3)/5 = 9/5 = 1` and `(3*2)/5 = 6/5 = 1`, summing to `2`, one short of `3`. More generally, for `tx_fees_streamed % 5 ∈ {1,2,3,4}`, `floor(3n/5) + floor(2n/5) = n - (n mod 5 == 1 or 2 ? ... )` — the maximum possible shortfall is bounded (at most a few micro-STX per block, well under the "up to 4" bound stated in the question, since `3n/5 + 2n/5 = n` exactly and only the two independent floors can each round down by less than 1, together losing at most `4/5` before flooring — concretely losses of 1 or 2 micro-STX depending on `n mod 5`).

This is invoked from `calculate_miner_reward` for `MinerPaymentTxFees::Epoch2` blocks, where `participant.streamed_tx_fees_confirmed()` and `parent.streamed_tx_fees_produced()` (or `participant.streamed_tx_fees_produced()` post-Epoch21) are computed as separate calls on the schedule and stored into separate `MinerReward` records (`tx_fees_streamed_confirmed` and `tx_fees_streamed_produced`). [2](#0-1) 

No code path re-derives the remainder or credits it anywhere (e.g., to the burn address or a separate ledger entry) — the microblock-fee STX corresponding to the rounding remainder is simply never minted/credited to any account, since these rewards are computed additively from the schedule's `tx_fees_streamed` field without any complementary "remainder" term.

### Impact Explanation
This causes at most a few micro-STX of streamed transaction fees to be permanently lost per epoch2 block with microblocks whose aggregate streamed fee is not a multiple of 5. No party gains the lost value — it is not double-paid and no double-spend or state-root divergence occurs, since both nodes computing rewards from the same recorded `tx_fees_streamed` value will independently compute the identical (slightly short) totals, so consensus / state-root determinism between honest nodes is preserved. This matches the "reward mis-payment bounded to fees" High category, not a Critical fund-theft/double-pay finding.

### Likelihood Explanation
This triggers deterministically on ordinary epoch2 microblock-confirming activity; no attacker privilege, majority stake, or malicious behavior is required — an attacker with a single miner slot (or even an honest miner) confirming any streamed-fee total not divisible by 5 will encounter this loss on essentially every affected block, since `MinerPaymentTxFees::Epoch2` remains reachable for pre-Nakamoto historical/legacy code paths. It is repeatable across every applicable block, but the value lost per instance is a bounded, tiny fraction of a fee (single-digit micro-STX), i.e., dust, not an economically meaningful griefing/theft vector.

### Recommendation
If exact conservation is desired, compute one share via subtraction from the other (e.g., `let confirmed = (streamed * 3) / 5; let produced = streamed - confirmed;`) so the two shares always sum exactly to `tx_fees_streamed`. Given this affects only frozen/legacy `Epoch2` fee-splitting logic (already noted in-code as "wrong, per #3140" for pre-2.1 epochs), and the impact is bounded dust loss rather than a security-critical divergence, this can be treated as a low-priority correctness cleanup rather than an urgent security fix.

### Proof of Concept
```rust
// stackslib/src/chainstate/stacks/tests/accounting.rs (new test)
use crate::chainstate::stacks::db::accounts::MinerPaymentSchedule;
use crate::chainstate::stacks::MinerPaymentTxFees;

#[test]
fn test_streamed_fee_split_rounding_loss() {
    for streamed in 1u128..=10000 {
        let mut sched = MinerPaymentSchedule::genesis(true);
        sched.tx_fees = MinerPaymentTxFees::Epoch2 { anchored: 0, streamed };
        let confirmed = sched.streamed_tx_fees_confirmed();
        let produced = sched.streamed_tx_fees_produced();
        if confirmed + produced != streamed {
            println!(
                "rounding loss at streamed={}: confirmed={}, produced={}, sum={}, lost={}",
                streamed, confirmed, produced, confirmed + produced,
                streamed - (confirmed + produced)
            );
        }
    }
    // Demonstrates confirmed+produced < tx_fees_streamed for streamed % 5 != 0,
    // e.g. streamed=3 => confirmed=1, produced=1, sum=2, lost=1.
}
```

### Citations

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L204-220)
```rust
    pub fn streamed_tx_fees_confirmed(&self) -> u128 {
        let tx_fees_streamed = match self.tx_fees {
            MinerPaymentTxFees::Epoch2 { streamed, .. } => streamed,
            MinerPaymentTxFees::Nakamoto { .. } => 0,
        };
        (tx_fees_streamed * 3) / 5
    }

    /// If this is a MinerPaymentSchedule for a miner who _produced_ a microblock stream, then
    /// this calculates the percentage of that stream this miner is entitled to
    pub fn streamed_tx_fees_produced(&self) -> u128 {
        let tx_fees_streamed = match self.tx_fees {
            MinerPaymentTxFees::Epoch2 { streamed, .. } => streamed,
            MinerPaymentTxFees::Nakamoto { .. } => 0,
        };
        (tx_fees_streamed * 2) / 5
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L906-945)
```rust
        let (tx_fees_anchored, parent_tx_fees_streamed_produced, tx_fees_streamed_confirmed) =
            if participant.miner {
                // only award tx fees to the miner, and only if the miner was not punished.
                // parent gets its produced tx fees regardless of punishment.

                match participant.tx_fees {
                    MinerPaymentTxFees::Epoch2 {
                        anchored,
                        streamed: _,
                    } => {
                        // if the payment type is Epoch2, then reward fees according to old Epoch2 rules
                        let anchored_fees = if !punished { anchored } else { 0 };
                        let parent_streamed_fees = if parent_block_epoch < StacksEpochId::Epoch21 {
                            // this is wrong, per #3140.  It should be
                            // `participant.streamed_tx_fees_produced()`, since
                            // `participant.tx_fees_streamed` contains the sum of the microblock
                            // transaction fees that `participant` confirmed (and thus `participant`'s
                            // parent produced).  But we're stuck with it for earlier epochs.
                            parent.streamed_tx_fees_produced()
                        } else {
                            participant.streamed_tx_fees_produced()
                        };
                        let streamed_confirmed_fees = if !punished {
                            participant.streamed_tx_fees_confirmed()
                        } else {
                            0
                        };
                        (anchored_fees, parent_streamed_fees, streamed_confirmed_fees)
                    }
                    MinerPaymentTxFees::Nakamoto { parent_fees } => {
                        // in nakamoto, tx fees in the payment schedule correspond to the
                        //  tx fees of the *parent tenure* (because the full tenure is only known
                        //  once the next tenure change occurs).
                        (0, parent_fees, 0)
                    }
                }
            } else {
                // users get no tx fees
                (0, 0, 0)
            };
```
