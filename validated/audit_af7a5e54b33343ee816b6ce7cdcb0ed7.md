### No vulnerability found for this question.

`PackedAncientStorage::pack` already guards against the exact scenario described. In the inner account-splitting loop, the break condition is `if new_size > ideal_size && bytes_total > 0 { full = true; break; }` — note the `bytes_total > 0` conjunct. [1](#0-0) 

This means that when starting a fresh packed storage (`bytes_total == 0`), a single oversized account (whose `stored_size()` alone exceeds `ideal_size`) is still unconditionally added — `partial_bytes_written += account_size; bytes_total = new_size; partial_inner_index_max_exclusive += 1;` — because the `bytes_total > 0` check is false, so `full` is never set for that first account. This guarantees `partial_inner_index_max_exclusive` strictly advances past that account, so `partial_inner_index` (set at line 1081) always progresses whenever there's at least one remaining account, and `accounts_to_write` is never empty while input remains. [2](#0-1) 

The outer loop only terminates via `if accounts_to_write.is_empty() { break; }`, and this only happens once `current_alive_accounts` is exhausted (all input consumed), since a non-empty entry always yields at least the oversized account into `accounts_to_write`. [3](#0-2) [4](#0-3) 

So an attacker-controlled oversized account (near `MAX_PERMITTED_DATA_LENGTH`) does not cause an infinite loop; it is instead packed alone into an oversized `PackedAncientStorage` (exceeding `ideal_size` for that one entry), and packing proceeds and terminates normally. The premise that "a single account's `stored_size()` alone exceeding `ideal_size`" prevents `partial_inner_index` from advancing is incorrect — the code explicitly special-cases `bytes_total == 0` to force progress.

### Citations

**File:** accounts-db/src/ancient_append_vecs.rs (L1031-1044)
```rust
            while !full && current_alive_accounts.is_some() {
                let alive_accounts = current_alive_accounts.unwrap();
                if partial_inner_index >= alive_accounts.accounts.len() {
                    // current_alive_accounts have all been written, so advance to next set from accounts_to_combine
                    current_alive_accounts = accounts_to_combine.next();
                    // reset partial progress since we're starting over with a new set of alive accounts
                    partial_inner_index = 0;
                    partial_bytes_written = Saturating(0);
                    continue;
                }
                let bytes_remaining_this_slot =
                    alive_accounts.bytes.saturating_sub(partial_bytes_written.0);
                let bytes_total_with_this_slot =
                    bytes_total.saturating_add(bytes_remaining_this_slot);
```

**File:** accounts-db/src/ancient_append_vecs.rs (L1053-1067)
```rust
                    while partial_inner_index_max_exclusive < alive_accounts.accounts.len() {
                        let account = alive_accounts.accounts[partial_inner_index_max_exclusive];
                        let account_size = account.stored_size();
                        let new_size = bytes_total.saturating_add(account_size);
                        if new_size > ideal_size && bytes_total > 0 {
                            full = true;
                            // partial_inner_index_max_exclusive is the index of the first account that puts us over the ideal size
                            // so, save it for next time
                            break;
                        }
                        // this account fits
                        partial_bytes_written += account_size;
                        bytes_total = new_size;
                        partial_inner_index_max_exclusive += 1;
                    }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L1070-1082)
```rust
                if partial_inner_index < partial_inner_index_max_exclusive {
                    // these accounts belong in the current packed storage we're working on
                    accounts_to_write.push((
                        alive_accounts.slot,
                        // maybe all alive accounts from the current or could be partial
                        &alive_accounts.accounts
                            [partial_inner_index..partial_inner_index_max_exclusive],
                    ));
                }
                // start next storage with the account we ended with
                // this could be the end of the current alive accounts or could be anywhere within that vec
                partial_inner_index = partial_inner_index_max_exclusive;
            }
```

**File:** accounts-db/src/ancient_append_vecs.rs (L1083-1094)
```rust
            if accounts_to_write.is_empty() {
                // if we returned without any accounts to write, then we have exhausted source data and have packaged all the storages we need
                break;
            }
            // we know the full contents of this packed storage now
            result.push(PackedAncientStorage {
                bytes: bytes_total as u64,
                accounts: accounts_to_write,
            });
        }
        result
    }
```
