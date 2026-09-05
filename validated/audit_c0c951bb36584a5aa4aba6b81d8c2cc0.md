### Title
`handle_poison_microblock` resolves poison reports by last-write-wins pubkey-hash→height lookup, mis-attributing equivocation slashing to the wrong tenure - ([File: stackslib/src/chainstate/stacks/db/transactions.rs])

### Summary
`handle_poison_microblock` determines which tenure's coinbase to punish by calling `get_microblock_pubkey_hash_height(&pubkh)`, which reads a Clarity data-map entry keyed solely by `pubkey_hash` (via `make_microblock_pubkey_height_key`) with no height/tenure component. If a miner's microblock signing key is reused across two tenures h1 < h2, `insert_microblock_pubkey_hash` at h2 overwrites the h1 mapping, so a valid poison report for an equivocation that actually occurred in h1's microblock stream resolves to h2, and `find_mature_miner_rewards`/`get_poison_microblock_report` slash or forfeit h2's coinbase instead of h1's.

### Finding Description
The equality that should hold is: *reward forfeited == the coinbase of the tenure whose miner actually double-signed at the reported sequence*. Because `get_microblock_pubkey_hash_height` and the underlying `ClarityDatabase::make_microblock_pubkey_height_key` index only by `pubkey_hash` [1](#0-0) , and `insert_microblock_pubkey_hash` writes/overwrites this single-valued map entry each time a new anchored block announces a `microblock_pubkey_hash` [2](#0-1) , nothing in block validation (`check_tenure_tx`, `common_validate_against_burnchain`, or any static header check) forbids two different tenures from declaring the same microblock public key hash in their headers. When the attacker (a miner who won both tenures h1 and h2, or who otherwise arranges key reuse) submits a poison-microblock transaction proving equivocation in h1's stream, `handle_poison_microblock` in `stackslib/src/chainstate/stacks/db/transactions.rs` recovers the pubkey hash from the two conflicting microblock headers and looks up its height via `get_microblock_pubkey_hash_height`, which returns h2 (the most recent write) rather than h1 (the tenure the reported headers actually belong to). The resulting `(reporter, seq)` record is then stored via `insert_microblock_poison` keyed on h2, and later `find_mature_miner_rewards` calls `get_poison_microblock_report(clarity_tx, reward_height)` for h2 — punishing/forfeiting h2's coinbase instead of h1's, even though the double-signature evidence and content are provably tied to h1's parent/tenure context.

### Impact Explanation
This causes a genuine block-reward mis-payment: either (a) an innocent miner at h2 has their legitimate coinbase forfeited/redirected to the reporter for an offense they did not commit, or (b) the actual offending miner at h1 evades the intended penalty because the system credits the punishment to the wrong height. Both are direct instances of "block-reward theft/double-payment/loss to the wrong tenure's miner," matching the Critical impact category defined in scope.

### Likelihood Explanation
The precondition is narrow but achievable by a single unprivileged miner: win (or otherwise control) two tenures h1 and h2 and reuse the same microblock private key across both — nothing in block header validation enforces microblock-key freshness per tenure. No majority stake, no other party's key, and no privileged role is required; the attacker only needs their own BTC/miner slot for two block-commits and control of one private key. The attack is repeatable each time key reuse occurs and a genuine equivocation is later reported.

### Recommendation
Change the microblock-pubkey-hash tracking to a height-indexed history (e.g., key by `(pubkey_hash, height)` or store a list of heights per pubkey hash) so that `handle_poison_microblock` can resolve the specific tenure height that actually signed the reported conflicting microblocks, rather than only the most recent tenure to reuse that hash. Alternatively, cross-check the reported microblock headers' parent-block/tenure linkage against the candidate height before accepting the poison report.

### Proof of Concept
Rust integration test outline (two-tenure harness):
1. Mine tenure at height h1 with miner key K, using microblock signing key `msk`; produce two conflicting microblocks at the same sequence number signed with `msk` (equivocation), but do not report yet.
2. Mine a second tenure at height h2 (same or different miner), reusing the same `msk`, so `insert_microblock_pubkey_hash` overwrites the `pubkey_hash -> height` mapping to h2.
3. Submit the poison-microblock transaction with the two conflicting headers from step 1 (h1's fork).
4. Assert `get_microblock_pubkey_hash_height(&pubkh) == h2` (broken side) while the headers are only valid for h1's stream (expected side: h1).
5. Advance chain past maturity window; call `find_mature_miner_rewards` for both h1 and h2; assert h2's coinbase is forfeited/redirected to the reporter and h1's coinbase (the actually-offending tenure) is paid in full — demonstrating the equality "reward slashed == offending tenure's reward" is violated.

### Citations

**File:** clarity/src/vm/database/clarity_db.rs (L1-1)
```rust
// Copyright (C) 2013-2020 Blockstack PBC, a public benefit corporation
```

**File:** stackslib/src/chainstate/stacks/db/mod.rs (L1-1)
```rust
// Copyright (C) 2013-2020 Blockstack PBC, a public benefit corporation
```
