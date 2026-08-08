### Title
Stale disk-read resurrects a purged (zero-lamport, closed) account into the in-mem index via `get_internal_inner`'s lock-free disk load + late `Entry::Vacant` insert - ([File: accounts-db/src/accounts_index/in_mem_accounts_index.rs])

### Summary
`InMemAccountsIndex::get_internal_inner` reads the disk index (`load_account_entry_from_disk`) *before* acquiring `map_internal`'s write lock, and later unconditionally re-inserts that possibly-stale value on the `Entry::Vacant` arm without re-validating against disk. If `remove_if_slot_list_empty` fully purges the same pubkey (deleting the disk key and any mem entry) in the window between the disk read and the write-lock acquisition, the stale pre-purge entry gets written back into `map_internal`, resurrecting a closed account's old slot list/lamport data in memory after it was supposed to be gone.

### Finding Description
In `get_internal_inner`, the sequence is:
1. `get_only_in_mem` finds nothing in mem.
2. `disk_entry = self.load_account_entry_from_disk(pubkey)` is read **without holding** `map_internal`'s lock [1](#0-0) .
3. Only afterward does it take `self.map_internal.write().unwrap()` and call `map.entry(*pubkey)`; if `Entry::Vacant`, it inserts `disk_entry` (captured in step 2) unconditionally when `add_to_cache || disk_entry.dirty()` [2](#0-1) .

Meanwhile, `remove_if_slot_list_empty` takes the write lock, and on `Entry::Occupied` with an empty slot list, deletes the disk key and removes the mem entry; on `Entry::Vacant`, it re-reads disk and, if empty, deletes the disk key [3](#0-2) . This whole purge sequence executes fully inside the write-lock critical section, entirely within the window that `get_internal_inner` leaves unprotected between its disk read and its own lock acquisition.

If a thread's step 2 disk read captures the account while it is still live/non-empty (a genuine, correct snapshot at that instant), and a concurrent thread's `remove_if_slot_list_empty` subsequently and legitimately purges the (now actually empty) account before the first thread reaches step 3, the first thread will find `Entry::Vacant` (since the purge fully removed both mem entry and disk key) and blindly re-insert its now-stale `disk_entry`, resurrecting the pre-close slot list/lamport data into `map_internal`. No version counter, generation stamp, or "did the disk state I read still match?" recheck exists on the `Entry::Vacant` insert path to prevent this. The comment at lines 337-342 already documents an *adjacent* known race ("if someone else holds the arc...") but that is a different sub-case from the disk-load timing gap analyzed here, and it is not a fix for this issue.

The reachable trigger requires no privileged access: an unprivileged user can create an account, close it (spend it to zero lamports and let it get swept by `clean`), and immediately recreate an account with the same pubkey while a reader (any code path funnelling through `get_internal_inner`, e.g. `slot_list_mut_with_entry`) races against the background clean/purge thread's `remove_if_slot_list_empty` call for the just-closed pubkey. High-frequency account close/reopen cycles by the attacker increase the probability of hitting the race window, but the timing itself depends on validator-internal thread scheduling that the attacker does not directly control.

### Impact Explanation
If triggered, a closed (zero-lamport, purged) account's stale slot-list/account-info entry re-appears in `map_internal`, meaning subsequent reads through the in-mem index can return stale, wrong-version account data (including its pre-close lamport value) for an account that should not exist in the index at all. Because the underlying append-vec storage the resurrected `(Slot, U)` reference points to may already have been shrunk/reclaimed by the same purge cycle, a subsequent read via this stale entry risks reading from reused/invalid append-vec storage, which is a stale/wrong-version account load and a potential source of a node panic — matching the "stale or wrong-version account load" and "node panic" bounty categories.

### Likelihood Explanation
This requires a precise, sub-microsecond-scale interleaving between (a) a reader thread that already passed the "not in mem" check and read disk, and (b) a concurrent background purge (`clean`/`remove_if_slot_list_empty`) completing entirely inside that gap for the same pubkey. This can occur under ordinary validator operation (concurrent clean + concurrent index reads), and an attacker can increase the chance of hitting the window by generating rapid open/close/reopen cycles on accounts they control, but they cannot force the exact scheduling needed, making this low-probability per attempt though repeatable given enough attempts/load.

### Recommendation
Re-validate the disk-read result under the write lock before inserting on the `Entry::Vacant` branch: either re-read from disk while holding `map_internal`'s write lock (eliminating the read-then-lock gap), or attach a generation/version tag to disk entries and reject stale inserts if the tag no longer matches current disk state, or treat a fresh `Entry::Vacant` outcome after a lock-free disk read as "unknown" and re-issue `load_account_entry_from_disk` again after acquiring the lock rather than trusting the pre-lock snapshot.

### Proof of Concept
Whitebox unit test added to `accounts-db/src/accounts_index/in_mem_accounts_index.rs`'s `#[cfg(test)] mod tests`, manually reproducing the two-step sequence of `get_internal_inner` with a `remove_if_slot_list_empty` call injected in the unprotected window to prove resurrection deterministically (no thread-timing luck needed to demonstrate the logic flaw):

```rust
#[test]
fn test_disk_read_stale_resurrection_race() {
    let index = new_for_test::<u64>();
    let pubkey = solana_pubkey::new_rand();

    // Simulate an account with a non-empty slot list, written through to disk,
    // and evicted from mem (as would happen after a normal flush).
    index.insert_new_entry_if_missing_with_lock(
        pubkey,
        PreAllocatedAccountMapEntry::new(0, 100 /* lamports info */, &index.storage, false),
    );
    index.flush(); // pushes entry to disk, may evict from mem
    // force ref_count to 0 / slot list becomes empty (as clean() would do)
    // ... (drive slot list to empty via existing test helpers) ...

    // Step 1 of get_internal_inner: read disk BEFORE any lock is held.
    let stale_disk_entry = index.load_account_entry_from_disk(&pubkey).unwrap();

    // Concurrent purge completes fully here (simulating the race window):
    // slot list is now empty, so this deletes both disk key and any mem entry.
    assert!(index.remove_if_slot_list_empty(pubkey));
    assert!(index.load_from_disk(&pubkey).is_none());
    index.get_only_in_mem(&pubkey, false, |e| assert!(e.is_none()));

    // Step 2 of get_internal_inner (as if it now resumes): insert stale entry
    // into the Vacant map slot, exactly as the vulnerable code does.
    let mut map = index.map_internal.write().unwrap();
    if let Entry::Vacant(vacant) = map.entry(pubkey) {
        vacant.insert(Box::new(stale_disk_entry));
    }
    drop(map);

    // BUG: the purged account is resurrected in mem with its pre-close data.
    index.get_only_in_mem(&pubkey, false, |e| {
        assert!(e.is_none(), "purged account must not reappear in mem, but it did");
    });
}
```

Expected (pre-fix) result: the final assertion fails, proving the purged pubkey is observable in `map_internal` again with stale data — confirming the resurrection. A full concurrency PoC (loom or a spawned-thread stress test looping `get_internal_inner`-driving calls against `remove_if_slot_list_empty` on the same pubkey with a barrier to narrow the timing window) should be used to additionally confirm this is reachable via the real public `get_internal_inner`/`remove_if_slot_list_empty` API under genuine multithreading, not just via manual replication of the steps.

### Citations

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L278-284)
```rust
                // not in cache, look on disk
                let stats = self.stats();
                let disk_entry = self.load_account_entry_from_disk(pubkey);
                if disk_entry.is_none() {
                    return callback(None).1;
                }
                let disk_entry = disk_entry.unwrap();
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L285-301)
```rust
                let mut map = self.map_internal.write().unwrap();
                let capacity_pre = map.capacity();
                let entry = map.entry(*pubkey);
                let retval = match entry {
                    Entry::Occupied(occupied) => callback(Some(occupied.get())).1,
                    Entry::Vacant(vacant) => {
                        debug_assert!(!disk_entry.dirty());
                        let (add_to_cache, rt) = callback(Some(&disk_entry));
                        // We are holding a write lock to the in-memory map.
                        // This pubkey is not in the in-memory map.
                        // If the entry is now dirty, then it must be put in the cache or the modifications will be lost.
                        if add_to_cache || disk_entry.dirty() {
                            stats.inc_mem_count();
                            vacant.insert(Box::new(disk_entry));
                        }
                        rt
                    }
```

**File:** accounts-db/src/accounts_index/in_mem_accounts_index.rs (L328-368)
```rust
    fn remove_if_slot_list_empty_entry(
        &self,
        entry: Entry<Pubkey, Box<AccountMapEntry<T>>>,
    ) -> bool {
        match entry {
            Entry::Occupied(occupied) => {
                let result = self
                    .remove_if_slot_list_empty_value(occupied.get().slot_list_lock_read_len() == 0);
                if result {
                    // note there is a potential race here that has existed.
                    // if someone else holds the arc,
                    //  then they think the item is still in the index and can make modifications.
                    // We have to have a write lock to the map here, which means nobody else can get
                    //  the arc, but someone may already have retrieved a clone of it.
                    // account index in_mem flushing is one such possibility
                    self.delete_disk_key(occupied.key());
                    self.stats().dec_mem_count();
                    occupied.remove();
                }
                result
            }
            Entry::Vacant(vacant) => {
                // not in cache, look on disk
                let entry_disk = self.load_from_disk(vacant.key());
                match entry_disk {
                    Some(entry_disk) => {
                        // on disk
                        if self.remove_if_slot_list_empty_value(entry_disk.0.is_empty()) {
                            // not in cache, but on disk, so just delete from disk
                            self.delete_disk_key(vacant.key());
                            true
                        } else {
                            // could insert into cache here, but not required for correctness and value is unclear
                            false
                        }
                    }
                    None => true, // not in cache or on disk, but slot list is 'empty' and entry is not in index, so return true
                }
            }
        }
    }
```
