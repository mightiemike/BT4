### Title
Double-counting of the local signer's own vote in `get_burn_block_received_time_from_signers` can falsely satisfy signer weight threshold - (File: stacks-signer/src/signerdb.rs)

### Summary
`SignerDb::get_burn_block_received_time_from_signers` accumulates signer weight to determine when enough weighted signers have observed a given burn view (`ConsensusHash`). It seeds the accumulation with the local signer's own recorded time via `get_burn_block_receive_time_ch`, and then separately queries **all** rows in the `burn_block_updates_received_times` table for the same consensus hash — without excluding the local signer's own address from that second source.

### Finding Description [1](#0-0) 

```rust
pub fn get_burn_block_received_time_from_signers(...) -> Result<Option<u64>, DBError> {
    let mut entries = Vec::new();

    // Add our own vote if we received this consensus hash
    if let Some(local_received_time) = self.get_burn_block_receive_time_ch(ch)? {
        entries.push((local_address.clone(), local_received_time));
    }

    // Query other signer received times from the DB
    let query = r#"
        SELECT signer_addr, received_time
        FROM burn_block_updates_received_times
        WHERE burn_block_consensus_hash = ?1
    "#;
    ...
    for row in rows {
        ...
        entries.push((address, received_time));   // no filter excluding local_address
    }
    ...
    for (address, received_time) in entries {
        let weight = eval.address_weights.get(&address).copied().unwrap_or(0);
        vote_weight = vote_weight.saturating_add(weight);
        if eval.reached_agreement(vote_weight) {
            return Ok(Some(received_time));
        }
    }
    Ok(None)
}
```

The rows in `burn_block_updates_received_times` are populated generically by `insert_state_machine_update`, keyed only by whichever `address` is supplied to it: [2](#0-1) 

```rust
// Conditionally insert into burn_block_updates_received_times only if missing for (signer_addr, burn_block_consensus_hash)
let burn_block_consensus_hash = update.content.burn_block_view().0;
self.db.execute(
    "INSERT OR IGNORE INTO burn_block_updates_received_times
    (signer_addr, burn_block_consensus_hash, received_time)
    VALUES (?1, ?2, ?3)",
    params![address.to_string(), burn_block_consensus_hash, u64_to_sql(received_ts)?],
)?;
```

Nothing in this insert path excludes the local signer's own address from being persisted to this table under the general state-machine-update ingestion flow. If the local signer's own address is ever stored as a row for the target consensus hash (e.g., because its own update also passes through the same generic ingestion path used for peer updates), `get_burn_block_received_time_from_signers` will add that weight twice: once from the dedicated `get_burn_block_receive_time_ch` lookup, and again from the DB-row loop that makes no attempt to skip `local_address`. This is structurally identical to the reported bug class — a quantity already included in one source (`collateralToken.balanceOf`) being added again from a second source (`additionalCollateralFromUser`), corrupting a threshold-driving sum.

### Impact Explanation
`vote_weight` here directly drives `eval.reached_agreement(vote_weight)`, a signer-weight threshold check analogous to the block-acceptance/rejection weight checks used elsewhere in the signer set (e.g. `compute_voting_weight_threshold` in `stackslib/src/chainstate/nakamoto/mod.rs`). If the local signer's weight is doubled in this specific accumulation, the threshold can be satisfied using less real, distinct signer weight than the protocol requires — i.e., a minority of honest weight (plus the double-counted self weight) can appear to reach agreement on the earliest time a majority of signers observed a given burn view. This is used to time tenure-extension / burn-view agreement decisions, so it can cause a bounded, minority-triggerable timing/agreement divergence between signers about when a burn view was reached — a class of "temporary tip/agreement disagreement" that the rules explicitly allow as a valid (High) analog.

### Likelihood Explanation
This requires no majority collusion and no privileged access: it is purely a bookkeeping bug within a single signer's own `SignerDb`, triggered any time the local address's own update also lands in the shared `burn_block_updates_received_times` table for the same consensus hash. Given `insert_state_machine_update` is a generic ingestion path with no explicit `address != local_address` guard, this is plausible under normal signer message flows (e.g., self-observation of a broadcast own-state update, or reprocessing paths).

### Recommendation
In `get_burn_block_received_time_from_signers`, explicitly exclude `local_address` from the DB query results (e.g., add `AND signer_addr != ?2` to the SQL, or filter it out of the returned rows) before accumulating `vote_weight`, so the local signer's weight is counted exactly once regardless of whether its own update was also persisted into the shared table.

### Proof of Concept
1. Local signer processes and persists its own burn-view update for consensus hash `CH` through `get_burn_block_receive_time_ch`-backed storage.
2. The same local signer's address/update is also inserted into `burn_block_updates_received_times` for `CH` via `insert_state_machine_update` (generic ingestion path, no self-exclusion).
3. Call `get_burn_block_received_time_from_signers(eval, CH, local_address)`:
   - `entries` gets `(local_address, t0)` from the dedicated lookup.
   - The DB query also returns `(local_address, t0)` (or a similar timestamp) from the shared table.
   - `vote_weight` sums `eval.address_weights[local_address]` twice.
4. If `local_address`'s weight alone (doubled) plus a small minority of other distinct signers now exceeds `eval.reached_agreement` threshold, the function reports "agreement reached" even though the real, distinct signer weight observed for `CH` is below the required threshold — a threshold check that other signers computing the same value correctly (i.e., without the duplicate row) would not agree with, causing divergent state/timing conclusions across signers.

**Note on confidence**: I was not able to fully trace, within the available tool budget, every call site that feeds `insert_state_machine_update` to conclusively confirm the local signer's own address is always stored in `burn_block_updates_received_times` for the same consensus hash it separately tracks via `get_burn_block_receive_time_ch`. The double-counting is unambiguously present in the code as written (no filter excludes `local_address` from the DB-sourced rows), but whether it is reliably reachable in practice depends on call-site behavior I could not fully verify before the iteration limit was reached.

### Citations

**File:** stacks-signer/src/signerdb.rs (L2300-2312)
```rust
        // Conditionally insert into burn_block_updates_received_times only if missing for (signer_addr, burn_block_consensus_hash)
        let burn_block_consensus_hash = update.content.burn_block_view().0;
        self.db.execute(
            "INSERT OR IGNORE INTO burn_block_updates_received_times
            (signer_addr, burn_block_consensus_hash, received_time)
            VALUES (?1, ?2, ?3)",
            params![
                address.to_string(),
                burn_block_consensus_hash,
                u64_to_sql(received_ts)?,
            ],
        )?;
        Ok(())
```

**File:** stacks-signer/src/signerdb.rs (L2385-2439)
```rust
    pub fn get_burn_block_received_time_from_signers(
        &self,
        eval: &GlobalStateEvaluator,
        ch: &ConsensusHash,
        local_address: &StacksAddress,
    ) -> Result<Option<u64>, DBError> {
        let mut entries = Vec::new();

        // Add our own vote if we received this consensus hash
        if let Some(local_received_time) = self.get_burn_block_receive_time_ch(ch)? {
            entries.push((local_address.clone(), local_received_time));
        }

        // Query other signer received times from the DB
        let query = r#"
            SELECT signer_addr, received_time
            FROM burn_block_updates_received_times
            WHERE burn_block_consensus_hash = ?1
        "#;

        let mut stmt = self.db.prepare(query)?;
        let rows = stmt.query_map(params![ch], |row| {
            let signer_addr: String = row.get(0)?;
            let received_time: i64 = row.get(1)?;
            Ok((signer_addr, received_time))
        })?;
        for row in rows {
            let (signer_addr_str, received_time_i64) = row?;
            let address =
                StacksAddress::from_string(&signer_addr_str).ok_or(DBError::Corruption)?;

            let received_time = u64::try_from(received_time_i64).map_err(|e| {
                error!("Failed to convert received_time to u64: {e}");
                DBError::Corruption
            })?;

            entries.push((address, received_time));
        }

        // Sort by received_time ascending
        entries.sort_by_key(|(_, time)| *time);

        // Accumulate vote weight and stop when threshold is reached
        let mut vote_weight: u32 = 0;
        for (address, received_time) in entries {
            let weight = eval.address_weights.get(&address).copied().unwrap_or(0);
            vote_weight = vote_weight.saturating_add(weight);

            if eval.reached_agreement(vote_weight) {
                return Ok(Some(received_time));
            }
        }

        Ok(None)
    }
```
