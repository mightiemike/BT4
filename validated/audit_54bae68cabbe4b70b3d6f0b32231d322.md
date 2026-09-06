Based on my analysis of the burn distribution and sortition code paths, I can now evaluate this claim.

**The equality being tested:** `total_burns_u128` (used to compute `BurnSamplePoint` range weights in `make_sortition_ranges`) == the true sum of each miner's effective `burns` value across the distribution.

**Tracing the arithmetic:**

1. Each `BurnSamplePoint.burns` originates from `LeaderBlockCommitOp.burn_fee`, which is a `u64` parsed directly from real Bitcoin transaction output values (satoshis) in `LeaderBlockCommitOp::parse_pox_waterfall_commits` / `parse_pre_pox_waterfall_commits`. [1](#0-0)  The reward-phase path even guards its one multiplication with `checked_mul(...).ok_or_else(|| op_error::ParseError)?`, rejecting the op outright on overflow at parse time. [2](#0-1) 

2. In `distribution.rs`, `make_min_median_distribution` computes `burns = min(median_burn, most_recent_burn)` per candidate as `u128`, so per-candidate values can never exceed a real BTC-output-derived `u64`. [3](#0-2) 

3. `get_total_burns` sums these using `try_fold` with `checked_add`, returning `None` on overflow rather than wrapping silently. [4](#0-3) 

4. `make_sortition_ranges` calls `.unwrap()` on that `Option`, with an explicit code comment asserting "this can't overflow -- there's no way we get that many (u64) burns". [5](#0-4)  If it *did* overflow, this would panic (a crash/DoS), not silently produce a wrong weight — so the claimed "weight computed == true summed burn" invariant is not silently broken; the node would halt instead.

5. Independently, at the higher `BurnchainStateTransition::total_burns()` / sortition level, overflow is explicitly checked and handled safely: an overflowing `total_burns()` or cumulative `last_burn_total` causes the code to treat the block as having **no sortition** rather than compute a corrupted weight. [6](#0-5) 

**Why this isn't reachable by a minority attacker:** `burn_fee` values are bounded by the actual satoshis spent in a Bitcoin transaction (realistically capped near Bitcoin's ~21M BTC / ~2.1×10^15 satoshi supply), and the number of concurrent commits in a mining-commitment window is small and bounded by burnchain block capacity. Summing bounded, real per-candidate `u64` burns across a bounded window cannot approach `u64::MAX` (~1.8×10^19) using only an attacker's own BTC/minority stake — this would require an economically infeasible amount of real burned bitcoin, which is out of scope per the rules (price/economic assumptions, theoretical findings). Additionally, every overflow-prone step (`checked_mul` in parsing, `checked_add` in `total_burns`/`get_total_burns`, `checked_add` in cumulative burn) already fails safe (rejects the op or denies sortition) rather than allowing a corrupted/wrapped weight to be used for sortition, so `append_chain_tip_snapshot` never receives a `BurnSamplePoint`/`BlockSnapshot` computed from a wrapped sum.

#No vulnerability found for this question.

### Citations

**File:** stackslib/src/chainstate/burn/operations/leader_block_commit.rs (L283-306)
```rust
        if output_0.amount == 0 {
            warn!("Invalid commit tx: waterfall commit output 0 has zero amount");
            return Err(op_error::InvalidInput);
        }

        let BurnchainRecipient { address, amount } = output_0;
        let apparent_sender = BurnchainSigner(
            outputs
                .get(1)
                .map(|out| {
                    out.as_ref()
                        .map(|out| out.address.clone().to_b58())
                        .unwrap_or("<undecodable-output>".to_string())
                })
                .unwrap_or("<no-change-output>".to_string()),
        );

        let sunset_burn = 0;
        Ok(CommitCalculation {
            commit_outs: vec![address],
            sunset_burn,
            burn_fee: amount,
            apparent_sender,
        })
```

**File:** stackslib/src/chainstate/burn/operations/leader_block_commit.rs (L388-398)
```rust
            // compute the total amount transferred/burned, and check that the burn amount
            //   is expected given the amount transferred.
            let burn_fee = pox_fee
                .expect("A 0-len output should have already errored")
                .checked_mul(u64::try_from(OUTPUTS_PER_COMMIT).expect(">2^64 outputs per commit")) // total commitment is the pox_amount * outputs
                .ok_or_else(|| op_error::ParseError)?;

            if burn_fee == 0 {
                warn!("Invalid commit tx: burn/transfer amount is 0");
                return Err(op_error::ParseError);
            }
```

**File:** stackslib/src/chainstate/burn/distribution.rs (L281-305)
```rust
                let all_burns: Vec<_> = linked_commits
                    .iter()
                    .map(|commit| {
                        if let Some(commit) = commit {
                            commit.op.burn_fee() as u128
                        } else {
                            // use 1 as the linked commit min. this gives a miner a _small_
                            //  chance of winning a block even if they haven't performed chained utxos yet
                            1
                        }
                    })
                    .collect();
                let most_recent_burn = all_burns[0];

                let mut sorted_burns = all_burns.clone();
                sorted_burns.sort();
                let median_burn = if window_size % 2 == 0 {
                    (sorted_burns[(window_size / 2) as usize]
                        + sorted_burns[(window_size / 2 - 1) as usize])
                        / 2
                } else {
                    sorted_burns[(window_size / 2) as usize]
                };

                let burns = cmp::min(median_burn, most_recent_burn);
```

**File:** stackslib/src/chainstate/burn/distribution.rs (L396-399)
```rust
        // total burns for valid blocks?
        // NOTE: this can't overflow -- there's no way we get that many (u64) burns
        let total_burns_u128 = BurnSamplePoint::get_total_burns(burn_sample).unwrap() as u128;
        let total_burns = Uint512::from_u128(total_burns_u128);
```

**File:** stackslib/src/chainstate/burn/distribution.rs (L429-438)
```rust
    /// Calculate the total amount of crypto destroyed in this burn distribution.
    /// Returns None if there was an overflow.
    pub fn get_total_burns(burn_dist: &[BurnSamplePoint]) -> Option<u64> {
        burn_dist
            .iter()
            .try_fold(0u64, |burns_so_far, sample_point| {
                let n = u64::try_from(sample_point.burns).ok()?;
                burns_so_far.checked_add(n)
            })
    }
```

**File:** stackslib/src/chainstate/burn/sortition.rs (L606-638)
```rust
        // It ignores user burns that don't match any block.
        let block_burn_total = match state_transition.total_burns() {
            Some(total) => {
                if total == 0 {
                    // no one burned, so no sortition
                    debug!(
                        "No transactions submitted burns in block";
                        "burn_block_height" => %block_height.to_string(),
                        "burn_block_hash" => %block_hash.to_string(),
                    );
                    return make_snapshot_no_sortition();
                } else {
                    total
                }
            }
            None => {
                // overflow -- treat as 0 (no sortition)
                warn!("Burn count exceeds maximum threshold");
                return make_snapshot_no_sortition();
            }
        };

        // total burn.  If this ever overflows, then just stall the chain and deny all future
        // sortitions (at least the chain will remain available to serve queries, but it won't be
        // able to make progress).
        let next_burn_total = match last_burn_total.checked_add(block_burn_total) {
            Some(new_total) => new_total,
            None => {
                // overflow.  Deny future sortitions
                warn!("Cumulative sortition burn has overflown.  Subsequent sortitions will be denied.");
                return make_snapshot_no_sortition();
            }
        };
```
