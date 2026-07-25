<thinking

Let me look at the exact storage_write logic to confirm how storage usage is tracked per entry.

### Title
Storage Cost Underestimate in `AddressRegistrar::register` Depletes Contract Free Balance, Eventually Breaking All Registrations - (`runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`)

---

### Summary

`AddressRegistrar::register` estimates storage cost as `20 + account_id.len()` bytes, but the actual on-chain storage charged by the NEAR runtime per new `LookupMap` entry is `65 + account_id.len()` bytes — a systematic underestimate of **45 bytes per registration**. Each call with the minimum required deposit reduces the contract's free balance (balance above storage stake) by `45 × storage_byte_cost ≈ 0.00045 NEAR`. An unprivileged attacker can exhaust this free balance by submitting many minimum-deposit registrations, eventually causing all future registrations to fail with `LackBalanceForState`.

---

### Finding Description

The deposit check at lines 49–61 computes:

```rust
let bytes_to_store = 20 + (account_id.len() as u128);
let required_deposit =
    NearToken::from_yoctonear(env::storage_byte_cost().as_yoctonear() * bytes_to_store);
``` [1](#0-0) 

This accounts only for the raw 20-byte address and the raw string bytes of `account_id`. It misses three components that the NEAR runtime **always** charges when a new key-value pair is written via `env::storage_write`:

**1. Key prefix bytes (1 byte)**
`LookupMap::new(StorageKey::Addresses)` uses the Borsh serialization of `StorageKey::Addresses` as its trie key prefix. `StorageKey::Addresses` is a unit enum variant at index 0; Borsh encodes it as a single byte `0x00`. The full trie key is therefore `[0x00] || borsh([u8;20])` = **21 bytes**, not 20.

**2. Borsh u32 length prefix for `AccountId` (4 bytes)**
Borsh serializes `AccountId` (a `String`) as a 4-byte little-endian u32 length followed by the string bytes. The stored value is therefore `4 + account_id.len()` bytes, not `account_id.len()`.

**3. `num_extra_bytes_record` (40 bytes)**
The NEAR runtime adds `num_extra_bytes_record = 40` to `current_storage_usage` for every new trie entry, as confirmed by the canonical test:

```rust
let cost_expected = data_record_cost + key.len + val.len;
assert_eq!(logic.storage_usage().unwrap(), cost_expected);
``` [2](#0-1) 

And by the `storage_remove` mirror path which subtracts the same three components:

```rust
self.result_state.current_storage_usage = self
    .result_state
    .current_storage_usage
    .checked_sub(
        value.len() as u64
            + key.len() as u64
            + storage_config.num_extra_bytes_record,
    )
``` [3](#0-2) 

**Actual storage per entry:** `21 (key) + (4 + account_id.len()) (value) + 40 (extra) = 65 + account_id.len()` bytes
**Estimated storage per entry:** `20 + account_id.len()` bytes
**Underestimate:** **45 bytes per entry**

At `storage_amount_per_byte = 10^19 yN` (the live mainnet value): [4](#0-3) 

the shortfall per registration is `45 × 10^19 yN = 4.5 × 10^20 yN ≈ 0.00045 NEAR`.

---

### Impact Explanation

After each successful registration, the contract's free balance (balance minus storage stake) decreases by ~0.00045 NEAR. The runtime enforces the storage stake invariant after every function call receipt:

```
account.balance >= account.storage_usage * storage_amount_per_byte
``` [5](#0-4) 

Once the contract's free balance drops below `45 × storage_byte_cost`, the next minimum-deposit `register` call will fail with `LackBalanceForState` because the deposit collected (`(20 + account_id.len()) × storage_byte_cost`) is insufficient to cover the actual storage increase (`(65 + account_id.len()) × storage_byte_cost`). All subsequent registrations are permanently blocked until the contract receives an out-of-band top-up — **contract execution flow breakage**.

---

### Likelihood Explanation

The attacker is fully unprivileged. Any account can call `register` with the minimum deposit. Each call costs the attacker `(20 + account_id.len()) × 10^19 yN` (e.g., ~0.0003 NEAR for a 10-byte account ID) while reducing the contract's free balance by ~0.00045 NEAR. The ratio of damage to attacker cost is ~1.5×. The attack is economically cheap and requires no special access.

---

### Recommendation

Replace the manual byte estimate with the exact components the runtime charges:

```rust
// Actual trie key = prefix_len + 20 (address bytes, no borsh length prefix for fixed arrays)
// Actual trie value = 4 (borsh u32 length prefix) + account_id.len()
// Plus num_extra_bytes_record per new entry
let prefix_len: u128 = 1; // StorageKey::Addresses borsh-encodes as 1 byte (variant 0)
let borsh_string_overhead: u128 = 4; // u32 length prefix
let extra_bytes_record: u128 = 40; // num_extra_bytes_record
let bytes_to_store = prefix_len + 20 + borsh_string_overhead
    + (account_id.len() as u128) + extra_bytes_record;
// = 65 + account_id.len()
```

Alternatively, use `env::storage_usage()` delta measurement (read before and after a dry-run insert) or expose `num_extra_bytes_record` via a host function and compute the exact key/value sizes from the SDK's actual serialization.

---

### Proof of Concept

**Byte-level arithmetic (no code needed):**

| Component | Bytes |
|---|---|
| `StorageKey::Addresses` borsh prefix | 1 |
| `[u8; 20]` borsh (no length prefix for fixed arrays) | 20 |
| `AccountId` borsh u32 length prefix | 4 |
| `AccountId` string bytes (e.g. `"alice.near"` = 10 chars) | 10 |
| `num_extra_bytes_record` | 40 |
| **Actual storage** | **75** |
| **Estimated storage** | **30** |
| **Shortfall** | **45** |

At `storage_byte_cost = 10^19 yN`, the shortfall is `4.5 × 10^20 yN ≈ 0.00045 NEAR` per registration. After `N` minimum-deposit registrations, the contract's free balance decreases by `N × 0.00045 NEAR`. When free balance reaches zero, the next `register` call fails with `LackBalanceForState`, permanently blocking all future registrations until an external top-up occurs. [6](#0-5)

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L48-61)
```rust
        // Must store the address and the account id
        let bytes_to_store = 20 + (account_id.len() as u128);
        let required_deposit =
            NearToken::from_yoctonear(env::storage_byte_cost().as_yoctonear() * bytes_to_store);
        let given_deposit = env::attached_deposit();
        // The caller must pay for the storage cost of registering.
        if given_deposit < required_deposit {
            let message = format!(
                "Insufficient deposit to cover storage cost. Given={} Expected={}",
                given_deposit.as_yoctonear(),
                required_deposit.as_yoctonear(),
            );
            env::panic_str(&message);
        }
```

**File:** runtime/near-vm-runner/src/logic/tests/storage_usage.rs (L6-15)
```rust
    let data_record_cost = logic_builder.fees_config.storage_usage_config.num_extra_bytes_record;
    let mut logic = logic_builder.build();
    let key = logic.internal_mem_write(b"foo");
    let val = logic.internal_mem_write(b"bar");

    logic.storage_write(key.len, key.ptr, val.len, val.ptr, 0).expect("storage write ok");

    let cost_expected = data_record_cost + key.len + val.len;

    assert_eq!(logic.storage_usage().unwrap(), cost_expected);
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L4322-4330)
```rust
                self.result_state.current_storage_usage = self
                    .result_state
                    .current_storage_usage
                    .checked_sub(
                        value.len() as u64
                            + key.len() as u64
                            + storage_config.num_extra_bytes_record,
                    )
                    .ok_or(InconsistentStateError::IntegerOverflow)?;
```

**File:** core/parameters/res/runtime_configs/parameters.yaml (L35-37)
```yaml
storage_amount_per_byte: 10000000000000000000 yN
storage_num_bytes_account: 100
storage_num_extra_bytes_record: 40
```

**File:** runtime/runtime/src/verifier.rs (L1-10)
```rust
use crate::action_validation::{validate_actions, validate_actions_with_mode};
use crate::config::TransactionCost;
use crate::near_primitives::account::Account;
use crate::{AccessKeyUpdate, PendingConstraints, TxVerdict, VerificationResult};
use near_crypto::PublicKey;
use near_parameters::RuntimeConfig;
use near_primitives::account::{AccessKey, FunctionCallPermission};
use near_primitives::errors::{
    DepositCostFailureReason, InvalidAccessKeyError, InvalidTxError, ReceiptValidationError,
};
```
