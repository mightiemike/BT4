[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** stackslib/src/chainstate/burn/distribution.rs (L293-341)
```rust
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

                let frequency = linked_commits.iter().fold(0u8, |count, commit_opt| {
                    if commit_opt.is_some() {
                        count
                            .checked_add(1)
                            .expect("infallable -- commit window exceeds u8::MAX")
                    } else {
                        count
                    }
                });

                let candidate = if let LinkedCommitIdentifier::Valid(op) =
                    linked_commits.remove(0).unwrap().op
                {
                    op
                } else {
                    unreachable!("BUG: first linked commit should always be valid");
                };
                assert_eq!(candidate.burn_fee as u128, most_recent_burn);

                debug!("Burn sample";
                       "txid" => %candidate.txid.to_string(),
                       "most_recent_burn" => %most_recent_burn,
                       "median_burn" => %median_burn,
                       "frequency" => frequency,
                       "all_burns" => %format!("{:?}", all_burns));

                BurnSamplePoint {
                    burns,
                    median_burn,
                    frequency,
                    range_start: Uint256::zero(), // To be filled in
                    range_end: Uint256::zero(),   // To be filled in
                    candidate,
                }
            })
```

**File:** stackslib/src/chainstate/burn/distribution.rs (L396-415)
```rust
        // total burns for valid blocks?
        // NOTE: this can't overflow -- there's no way we get that many (u64) burns
        let total_burns_u128 = BurnSamplePoint::get_total_burns(burn_sample).unwrap() as u128;
        let total_burns = Uint512::from_u128(total_burns_u128);

        // determine range start/end for each sample.
        // Use fixed-point math on an unsigned 512-bit number --
        //   * the upper 256 bits are the integer
        //   * the lower 256 bits are the fraction
        // These range fields correspond to ranges in the 32-byte hash space
        let mut burn_acc = Uint512::zero();
        let mut last_sample_range_end = Uint256::zero();
        for sample_step in burn_sample.iter_mut() {
            sample_step.range_start = last_sample_range_end;

            burn_acc = burn_acc + Uint512::from_u128(sample_step.burns);
            sample_step.range_end =
                ((Uint512::from_uint256(&Uint256::max()) * burn_acc) / total_burns).to_uint256();
            last_sample_range_end = sample_step.range_end;
        }
```

**File:** stackslib/src/chainstate/burn/distribution.rs (L431-438)
```rust
    pub fn get_total_burns(burn_dist: &[BurnSamplePoint]) -> Option<u64> {
        burn_dist
            .iter()
            .try_fold(0u64, |burns_so_far, sample_point| {
                let n = u64::try_from(sample_point.burns).ok()?;
                burns_so_far.checked_add(n)
            })
    }
```
