### No vulnerability found for this question.

The premise that the `block_header.consensus_hash == tenure_payload.tenure_consensus_hash` equality is "enforced separately in `check_tenure_tx` but potentially bypassed if called out of order" is incorrect. `check_nakamoto_tenure` itself performs this exact check as its very first statement, unconditionally, before any other logic (including the `Extended` cause branch referenced in the question): [1](#0-0) 

This means a tenure-extend payload with `tenure_consensus_hash = CH_0` attached to a block whose `block_header.consensus_hash = CH_1` is rejected (`Ok(None)`) at line 660-665 before ever reaching the `Extended` cause self-equality check at lines 789-805 [2](#0-1) . There is no code path in `advance_nakamoto_tenure` or elsewhere that calls the `Extended`-cause equality logic without first passing through this header-consensus-hash guard — `advance_nakamoto_tenure` calls `check_nakamoto_tenure` directly and aborts with `InvalidStacksTransaction` if it returns `None` [3](#0-2) .

Additionally, even hypothetically bypassing that first check, the payload's `tenure_consensus_hash` (CH_0) would need to pass `check_valid_consensus_hash` and match the `parent_tenure.tenure_id_consensus_hash` obtained via `get_ongoing_tenure` on `block_header.parent_block_id` [4](#0-3) , which for a block built on the CH_1 tenure would resolve to CH_1, not CH_0 — providing a second independent guard against the described replay.

Since the equality `block_header.consensus_hash == tenure_payload.tenure_consensus_hash` is enforced directly inside `check_nakamoto_tenure` (not merely in a separately-callable `check_tenure_tx`), there is no ordering issue that could let a stale/finished tenure's consensus hash be replayed into `insert_nakamoto_tenure`, and thus no corruption of `get_nakamoto_tenure_length` or double coinbase-height counting is reachable via this path.

### Citations

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L659-666)
```rust
        // block header must match this tenure
        if block_header.consensus_hash != tenure_payload.tenure_consensus_hash {
            warn!("Invalid tenure-change (or block) -- mismatched consensus hash";
                  "tenure_payload.tenure_consensus_hash" => %tenure_payload.tenure_consensus_hash,
                  "block_header.consensus_hash" => %block_header.consensus_hash
            );
            return Ok(None);
        }
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L765-771)
```rust
        let Some(parent_tenure) =
            Self::get_ongoing_tenure(headers_conn, &block_header.parent_block_id)?
        else {
            // not building off of a previous Nakamoto tenure.  This is the first tenure change.  It should point to an epoch
            // 2.x block.
            return Self::check_first_nakamoto_tenure_change(headers_conn.sqlite(), tenure_payload);
        };
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L789-806)
```rust
            TenureChangeCause::Extended
            | TenureChangeCause::ExtendedRuntime
            | TenureChangeCause::ExtendedReadCount
            | TenureChangeCause::ExtendedReadLength
            | TenureChangeCause::ExtendedWriteCount
            | TenureChangeCause::ExtendedWriteLength => {
                // tenure extensions don't begin a new tenure (since the miner isn't changing), so
                // the tenure consensus hash must be the same as the previous tenure consensus hash
                if tenure_payload.tenure_consensus_hash != tenure_payload.prev_tenure_consensus_hash
                {
                    warn!("Invalid tenure-change: tenure extension tries to start a new tenure";
                          "tenure_consensus_hash" => %tenure_payload.tenure_consensus_hash,
                          "prev_tenure_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash,
                    );
                    return Ok(None);
                }
            }
        };
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L865-871)
```rust
        if Self::check_nakamoto_tenure(headers_tx, handle, &block.header, tenure_payload)?.is_none()
        {
            return Err(ChainstateError::InvalidStacksTransaction(
                "Invalid tenure tx".into(),
                false,
            ));
        };
```
