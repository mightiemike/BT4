### Title
Stale `AnnounceAccount` replay after node restart overwrites newer routing-table entry via epoch-check bypass in `AnnounceAccountCache::add_accounts` — (File: `chain/network/src/announce_accounts/mod.rs`)

### Summary

After a node restart the in-memory `account_peers_broadcasted` LRU cache is empty. The epoch-ordering guard in `ViewClientActor` is only applied when that cache has a prior entry for the account. Any peer can replay a validly-signed `AnnounceAccount` from an older epoch; it passes signature verification and is unconditionally written to both the in-memory routing table and the on-disk store, overwriting a newer entry that was persisted before the restart.

### Finding Description

The `AnnounceAccount` update path has two intended layers of protection against stale entries.

**Layer 1 — epoch hint construction** (`handle_sync_routing_table`):

For each incoming announcement, the network layer fetches the last-broadcasted `epoch_id` from `account_peers_broadcasted` (an in-memory LRU cache) and pairs it with the announcement as `last_epoch`:

```rust
// chain/network/src/peer/peer_actor.rs  ~L1408-1418
let old = network_state
    .account_announcements
    .get_broadcasted_announcements(rtu.accounts.iter().map(|a| &a.account_id));
let accounts: Vec<(AnnounceAccount, Option<EpochId>)> = rtu
    .accounts
    .into_iter()
    .map(|aa| {
        let id = aa.account_id.clone();
        (aa, old.get(&id).map(|old| old.epoch_id))
    })
    .collect();
``` [1](#0-0) 

**Layer 2 — epoch comparison** (`ViewClientActor`):

```rust
// chain/client/src/view_client_actor.rs  ~L1780-1784
if let Some(last_epoch) = last_epoch {
    match self.epoch_manager.compare_epoch_id(&announce_account.epoch_id, &last_epoch) {
        Ok(Ordering::Greater) => {}
        _ => continue,
    }
}
``` [2](#0-1) 

When `last_epoch` is `None` the entire epoch comparison is skipped; only the signature check runs.

**The root cause — `account_peers_broadcasted` is never seeded from disk:**

`account_peers_broadcasted` is an in-memory `LruCache` that starts empty on every process start:

```rust
// chain/network/src/announce_accounts/mod.rs  L51-57
Self(Mutex::new(Inner {
    account_peers: LruCache::new(...),
    account_peers_broadcasted: LruCache::new(...),
    store,
}))
``` [3](#0-2) 

`get_broadcasted_announcements` reads only from `account_peers_broadcasted`, never from the on-disk store:

```rust
// chain/network/src/announce_accounts/mod.rs  L107-117
pub(crate) fn get_broadcasted_announcements<'a>(
    &'a self,
    account_ids: impl Iterator<Item = &'a AccountId>,
) -> HashMap<AccountId, AnnounceAccount> {
    let mut inner = self.0.lock();
    account_ids
        .filter_map(|id| {
            inner.account_peers_broadcasted.get(id).map(|a| (id.clone(), a.clone()))
        })
        .collect()
}
``` [4](#0-3) 

After a restart the map is always empty, so `last_epoch` is `None` for every account, and Layer 2 is never reached.

**Unconditional overwrite in `add_accounts`:**

After the `ViewClientActor` passes the announcement through (signature-only check), `add_accounts` checks only whether the incoming `epoch_id` is byte-identical to what was already broadcasted — not whether it is newer than what is on disk:

```rust
// chain/network/src/announce_accounts/mod.rs  L73-85
if inner.account_peers_broadcasted.get(account_id).map(|x| &x.epoch_id)
    == Some(epoch_id)
{
    continue;
}
inner.account_peers.put(account_id.clone(), announcement.clone());
inner.account_peers_broadcasted.put(account_id.clone(), announcement.clone());
inner.store.set_account_announcement(account_id, &announcement);
``` [5](#0-4) 

Because `account_peers_broadcasted` is empty after a restart, the guard always evaluates to `None == Some(epoch_id)` → `false`, and the stale announcement is unconditionally written to both the in-memory cache and the on-disk store, overwriting any newer entry that was persisted before the restart.

The `AnnounceAccount` signature covers only `(account_id, peer_id, epoch_id)`:

```rust
// core/primitives/src/network.rs  L87-93
fn build_header_hash(
    account_id: &AccountId,
    peer_id: &PeerId,
    epoch_id: &EpochId,
) -> CryptoHash {
    CryptoHash::hash_borsh((account_id, peer_id, epoch_id))
}
``` [6](#0-5) 

Old announcements carry valid signatures forever; they never expire.

### Impact Explanation

`AnnounceAccount` is the sole mechanism by which nodes learn which `peer_id` to route transactions to for a given validator `account_id`. If a validator's routing entry is overwritten with an old `peer_id`, all forwarded transactions miss the validator. The validator fails to include them in chunks, accumulates missed-chunk penalties, and can be kicked out of the active set within a single epoch. Because `AnnounceAccount` messages are gossiped to the entire network, every peer that was online during the old epoch holds a copy of the old announcement and can execute this attack. The attack window lasts until the validator re-broadcasts a fresh announcement (up to `ttl_account_id_router / 2` ≈ 30 minutes in production). Targeting multiple validators simultaneously could stall block production.

### Likelihood Explanation

Node restarts are routine (binary upgrades, crashes, maintenance). Any peer that was connected during a past epoch has a stored copy of old `AnnounceAccount` messages — no special privilege is required. The attacker only needs to detect a restart (observable via connection events) and immediately send a `SyncRoutingTable` message containing the old announcement before the validator re-broadcasts. The attack requires no on-chain action and leaves no on-chain trace.

### Recommendation

Seed `account_peers_broadcasted` from the on-disk store at startup, or change `get_broadcasted_announcements` to fall back to the store when the in-memory cache has no entry for an account. This ensures `last_epoch` is always `Some` for any account that has a persisted announcement, so the epoch-ordering guard in `ViewClientActor` is never bypassed.

### Proof of Concept

1. Validator V has `peer_id = P1` in epoch E1. `AnnounceAccount(V, P1, E1)` is gossiped to the network. Attacker A stores it.
2. V rotates to `peer_id = P2` in epoch E2 (E2 > E1). `AnnounceAccount(V, P2, E2)` is gossiped. Node N persists it to disk.
3. Node N restarts. `account_peers_broadcasted` is empty.
4. A sends `SyncRoutingTable { accounts: [AnnounceAccount(V, P1, E1)] }` to N.
5. `handle_sync_routing_table` calls `get_broadcasted_announcements` → empty map → `last_epoch = None` for V.
6. `ViewClientActor` skips the epoch comparison. `check_signature_account_announce` passes (E1 was a valid epoch for V, signature is correct).
7. `add_accounts` is called. `account_peers_broadcasted` has no entry for V → guard evaluates `None == Some(E1)` → `false` → announcement is written to cache and disk, overwriting `AnnounceAccount(V, P2, E2)`.
8. N now routes all transactions for V to P1. V misses chunks; after sufficient misses V is kicked out of the validator set.

### Citations

**File:** chain/network/src/peer/peer_actor.rs (L1404-1418)
```rust
        // For every announce we received, we fetch the last announce with the same account_id
        // that we already broadcasted. Client actor will both verify signatures of the received announces
        // as well as filter out those which are older than the fetched ones (to avoid overriding
        // a newer announce with an older one).
        let old = network_state
            .account_announcements
            .get_broadcasted_announcements(rtu.accounts.iter().map(|a| &a.account_id));
        let accounts: Vec<(AnnounceAccount, Option<EpochId>)> = rtu
            .accounts
            .into_iter()
            .map(|aa| {
                let id = aa.account_id.clone();
                (aa, old.get(&id).map(|old| old.epoch_id))
            })
            .collect();
```

**File:** chain/client/src/view_client_actor.rs (L1780-1784)
```rust
            if let Some(last_epoch) = last_epoch {
                match self.epoch_manager.compare_epoch_id(&announce_account.epoch_id, &last_epoch) {
                    Ok(Ordering::Greater) => {}
                    _ => continue,
                }
```

**File:** chain/network/src/announce_accounts/mod.rs (L50-58)
```rust
    pub fn new(store: store::Store) -> Self {
        Self(Mutex::new(Inner {
            account_peers: LruCache::new(NonZeroUsize::new(ANNOUNCE_ACCOUNT_CACHE_SIZE).unwrap()),
            account_peers_broadcasted: LruCache::new(
                NonZeroUsize::new(ANNOUNCE_ACCOUNT_CACHE_SIZE).unwrap(),
            ),
            store,
        }))
    }
```

**File:** chain/network/src/announce_accounts/mod.rs (L73-85)
```rust
            // We skip broadcasting stuff that is already broadcasted.
            if inner.account_peers_broadcasted.get(account_id).map(|x| &x.epoch_id)
                == Some(epoch_id)
            {
                continue;
            }

            inner.account_peers.put(account_id.clone(), announcement.clone());
            inner.account_peers_broadcasted.put(account_id.clone(), announcement.clone());

            // Add account to store.
            inner.store.set_account_announcement(account_id, &announcement);
            res.push(announcement);
```

**File:** chain/network/src/announce_accounts/mod.rs (L107-117)
```rust
    pub(crate) fn get_broadcasted_announcements<'a>(
        &'a self,
        account_ids: impl Iterator<Item = &'a AccountId>,
    ) -> HashMap<AccountId, AnnounceAccount> {
        let mut inner = self.0.lock();
        account_ids
            .filter_map(|id| {
                inner.account_peers_broadcasted.get(id).map(|a| (id.clone(), a.clone()))
            })
            .collect()
    }
```

**File:** core/primitives/src/network.rs (L87-93)
```rust
    fn build_header_hash(
        account_id: &AccountId,
        peer_id: &PeerId,
        epoch_id: &EpochId,
    ) -> CryptoHash {
        CryptoHash::hash_borsh((account_id, peer_id, epoch_id))
    }
```
