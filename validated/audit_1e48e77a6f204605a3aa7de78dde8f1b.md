[1](#0-0) [2](#0-1)

### Citations

**File:** stackslib/src/net/download/nakamoto/tenure.rs (L142-155)
```rust
        let invbits = invs.tenures_inv.get(&rc)?;
        let mut tenure_block_ids = AvailableTenures::new();
        let mut last_tenure = 0;
        let mut last_tenure_ch = None;
        debug!("Find available tenures in inventory {:?} rc {}", invs, rc);
        for (i, wt) in wanted_tenures.iter().enumerate() {
            debug!("consider wanted tenure which starts with i={} {:?}", i, &wt);

            // advance to next tenure-start sortition
            let bit = u16::try_from(i).expect("FATAL: more sortitions than u16::MAX");
            if !invbits.get(bit).unwrap_or(false) {
                debug!("i={} bit not set", i);
                continue;
            }
```

**File:** stackslib/src/net/download/nakamoto/tenure.rs (L186-196)
```rust
            let tenure_start_end = TenureStartEnd::new(
                wt.tenure_id_consensus_hash.clone(),
                wt.burn_height,
                wt_start.tenure_id_consensus_hash.clone(),
                wt_start.winning_block_id.clone(),
                wt_end.tenure_id_consensus_hash.clone(),
                wt_end.winning_block_id.clone(),
                rc,
                rc,
                wt.processed,
            );
```
