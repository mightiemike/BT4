### Title
Unchecked `wrapping_add` in `BankHashStats::update`/`accumulate` silently corrupts persisted account statistics on overflow - (File: `runtime/src/bank.rs`)

### Summary
`BankHashStats::update` and `BankHashStats::accumulate` in [1](#0-0)  use `wrapping_add` to accumulate `total_data_len` and `num_lamports_stored` across all accounts touched in a slot. Like the `add` opcode in the zkSync bootloader report, `wrapping_add` performs the addition with no overflow detection: on overflow the accumulated value silently wraps to a small/zero value instead of panicking or saturating, exactly the bug class described in the external report (`refundGas := add(refundGas, reservedGas)` silently wrapping).

### Finding Description
`BankHashStats` is documented as "Account stats for computing the bank hash" and "This struct is serialized and stored in the snapshot" [2](#0-1) . Its `update` method is invoked once per modified/stored account to accumulate `total_data_len` and `num_lamports_stored`:

```rust
self.total_data_len = self.total_data_len.wrapping_add(account.data().len() as u64);
...
self.num_lamports_stored = self.num_lamports_stored.wrapping_add(account.lamports());
``` [3](#0-2) 

`accumulate` similarly wraps when merging per-slot stats into the running total:
```rust
self.total_data_len = self.total_data_len.wrapping_add(other.total_data_len);
...
self.num_lamports_stored = self.num_lamports_stored.wrapping_add(other.num_lamports_stored);
``` [4](#0-3) 

Elsewhere in the same codebase, the project consistently treats capitalization-style sums with `checked_add` and an explicit panic ("capitalization cannot overflow") precisely to detect this class of bug, e.g. in `accounts_db.rs`'s index-generation accumulator and duplicate-pubkey visiting code [5](#0-4) [6](#0-5) [7](#0-6) . `BankHashStats` deliberately deviates from this pattern by using `wrapping_add`, which means a sufficiently large `total_data_len` or `num_lamports_stored` accumulation (across many accounts/slots over the life of a validator, or via a single pathological transaction/epoch with huge account data or lamport totals) will silently wrap around `u64::MAX` with no panic, no log, and no detectable error — mirroring the unguarded `add` overflow in the reported zkSync bootloader bug.

### Impact Explanation
`BankHashStats` is persisted in snapshots (confirmed via multiple references in `runtime/src/serde_snapshot.rs`) and is documented as being used for "computing the bank hash." If `num_lamports_stored`/`total_data_len` wrap silently, the persisted per-bank statistics will diverge between an honest node that computed them incrementally over a long uptime versus one that rebuilt/replayed state from a snapshot at a different point, or between two nodes whose account update ordering/batching differs enough to change wrap timing. This falls into the "honest-node snapshot-vs-replay mismatch" / "hash divergence" impact category the analog rules call out, since a corrupted `BankHashStats` value would not match a freshly recomputed value from account storage after snapshot restore, producing silent, hard-to-diagnose validation/consistency defects rather than a clean panic.

### Likelihood Explanation
Triggering this specifically requires accumulating lamports or data length past `u64::MAX` in a single running `BankHashStats` counter. `num_lamports_stored` is bounded in practice by total system lamport supply (well under `u64::MAX`), making direct lamport overflow unlikely in the near term. `total_data_len`, however, accumulates account data lengths (not lamports) across every account write processed in a slot/accumulation window, and unlike capitalization, there is no consensus-level cap analogous to total lamport supply forcing it to stay bounded — high account-data churn over enough writes could approach the boundary over long timeframes. This is a genuine unguarded-arithmetic code smell consistent with the reported bug class, though in this codebase's current state it is a lower-likelihood, longer-horizon risk compared to the immediately exploitable zkSync scenario (which involved a single, operator-controlled value).

### Recommendation
Replace `wrapping_add` with `checked_add` (panicking, consistent with the rest of the capitalization-tracking code in `accounts_db.rs`) or `saturating_add` (if wrap-to-zero-equivalent silent corruption should instead be replaced with a detectable saturation ceiling) in both `BankHashStats::update` and `BankHashStats::accumulate`:
```diff
- self.total_data_len = self.total_data_len.wrapping_add(account.data().len() as u64);
+ self.total_data_len = self.total_data_len.checked_add(account.data().len() as u64)
+     .expect("total_data_len cannot overflow");
- self.num_lamports_stored = self.num_lamports_stored.wrapping_add(account.lamports());
+ self.num_lamports_stored = self.num_lamports_stored.checked_add(account.lamports())
+     .expect("num_lamports_stored cannot overflow");
```
Apply the analogous change to `accumulate`.

### Proof of Concept
1. Construct a sequence of account stores/updates (or repeatedly call `BankHashStats::update`/`accumulate`) whose `data().len()` values sum to `u64::MAX + 1` (or whose `lamports()` sum overflows `u64`).
2. Observe that `total_data_len` (or `num_lamports_stored`) silently wraps to a small value instead of panicking, unlike the equivalent capitalization accumulation paths in `accounts_db.rs` (e.g. `IndexGenerationAccumulator::accumulate` at [8](#0-7) ), which explicitly `.expect("capitalization cannot overflow")` on the same class of addition.
3. Because `BankHashStats` is serialized into snapshots, this silently corrupted value would persist and diverge from a value independently recomputed after replay/snapshot restore, with no error surfaced — the exact "silent overflow via unchecked add" failure mode described in the source report.

### Citations

**File:** runtime/src/bank.rs (L1100-1111)
```rust
/// Account stats for computing the bank hash
/// This struct is serialized and stored in the snapshot.
#[repr(C)]
#[cfg_attr(feature = "frozen-abi", derive(AbiExample, StableAbi, StableAbiSample))]
#[derive(Clone, Default, Debug, Serialize, Deserialize, PartialEq, Eq, SchemaRead, SchemaWrite)]
pub struct BankHashStats {
    pub num_updated_accounts: u64,
    pub num_removed_accounts: u64,
    pub num_lamports_stored: u64,
    pub total_data_len: u64,
    pub num_executable_accounts: u64,
}
```

**File:** runtime/src/bank.rs (L1113-1136)
```rust
impl BankHashStats {
    pub fn update<T: ReadableAccount>(&mut self, account: &T) {
        if account.lamports() == 0 {
            self.num_removed_accounts += 1;
        } else {
            self.num_updated_accounts += 1;
        }
        self.total_data_len = self
            .total_data_len
            .wrapping_add(account.data().len() as u64);
        if account.executable() {
            self.num_executable_accounts += 1;
        }
        self.num_lamports_stored = self.num_lamports_stored.wrapping_add(account.lamports());
    }
    pub fn accumulate(&mut self, other: &BankHashStats) {
        self.num_updated_accounts += other.num_updated_accounts;
        self.num_removed_accounts += other.num_removed_accounts;
        self.total_data_len = self.total_data_len.wrapping_add(other.total_data_len);
        self.num_lamports_stored = self
            .num_lamports_stored
            .wrapping_add(other.num_lamports_stored);
        self.num_executable_accounts += other.num_executable_accounts;
    }
```

**File:** accounts-db/src/accounts_db.rs (L445-459)
```rust
    fn accumulate(&mut self, mut other: Self) {
        self.insert_time_us += other.insert_time_us;
        self.num_accounts += other.num_accounts;
        self.accounts_data_len += other.accounts_data_len;
        self.all_accounts_are_zero_lamports_slots += other.all_accounts_are_zero_lamports_slots;
        self.slots_with_only_zero_lamport_accounts
            .append(&mut other.slots_with_only_zero_lamport_accounts);
        self.num_did_not_exist += other.num_did_not_exist;
        self.num_existed_in_mem += other.num_existed_in_mem;
        self.num_existed_on_disk += other.num_existed_on_disk;
        self.lt_hash.mix_in(&other.lt_hash);
        self.capitalization = self
            .capitalization
            .checked_add(other.capitalization)
            .expect("capitalization cannot overflow");
```

**File:** accounts-db/src/accounts_db.rs (L5762-5766)
```rust
                // SAFETY: The bank capitalization field is a u64, so the lamport sum of
                // all accounts modified in a single slot must fit into a u64.
                capitalization = capitalization
                    .checked_add(account.lamports())
                    .expect("capitalization cannot overflow");
```

**File:** accounts-db/src/accounts_db.rs (L6270-6273)
```rust
                            capitalization_from_duplicates = capitalization_from_duplicates
                                .checked_add(u128::from(lamports))
                                .expect("capitalization cannot overflow");
                        });
```
