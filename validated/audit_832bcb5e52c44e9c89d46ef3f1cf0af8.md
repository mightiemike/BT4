### Title
`ClassHashStorageWriter::set_executable_class_hash_v2` Uses `upsert` Despite Documenting "Error on Duplicate" — Silent Overwrite of `stateless_compiled_class_hash_v2` Mapping - (File: `crates/apollo_storage/src/class_hash.rs`)

---

### Summary

The `ClassHashStorageWriter` trait documents that `set_executable_class_hash_v2` **"returns an error if the class hash already exists"**, but the implementation calls `table.upsert(...)` — which silently overwrites any existing `ClassHash → CompiledClassHash` (v2, Blake-based) entry. This broken write-once invariant means the `stateless_compiled_class_hash_v2` table can be silently mutated after initial population, producing a wrong `CompiledClassHash` v2 in state diffs and OS proof verification without any error signal.

---

### Finding Description

In `crates/apollo_storage/src/class_hash.rs`, the trait contract and implementation diverge:

```
/// Inserts the executable class hash corresponding to the given class hash.
/// An error is returned if the class hash already exists.   ← documented contract
fn set_executable_class_hash_v2(self, class_hash: &ClassHash,
    executable_class_hash_v2: CompiledClassHash) -> StorageResult<Self>;
``` [1](#0-0) 

The implementation:

```rust
table.upsert(self.txn(), class_hash, &executable_class_hash_v2)?;
``` [2](#0-1) 

`upsert` uses `WriteFlags::UPSERT` (unconditional overwrite), while `insert` uses `WriteFlags::NO_OVERWRITE` (returns `KeyAlreadyExists` on collision): [3](#0-2) 

The `stateless_compiled_class_hash_v2` table is explicitly described as a **separate, stateless table** outside the normal block-scoped storage, intended to be written once per class: [4](#0-3) 

**Call path 1 — `ClassManager::add_class`**: Has an application-level guard that reads back the existing hash before writing. However, this guard is in the class manager layer, not the storage layer. The storage layer itself provides no protection. [5](#0-4) 

**Call path 2 — `native_blockifier/src/storage.rs`**: Calls `set_executable_class_hash_v2` directly for each "undeclared" class, where "undeclared" is determined by checking the main Papyrus state table (`get_class_definition_at`), **not** by checking whether the `stateless_compiled_class_hash_v2` entry already exists. These are two separate tables. [6](#0-5) 

**Call path 3 — CASM hash migration**: The `migrate_classes_to_v2_casm_hash` OS function updates `contract_class_changes` from v1 (Poseidon) to v2 (Blake) hashes. The Rust-side `set_compiled_class_hash_migration` writes the new v2 hash into `CachedState`. If the `stateless_compiled_class_hash_v2` table is subsequently re-populated for a migrated class (e.g., during replay or re-sync), the `upsert` will silently overwrite the migrated v2 hash with whatever value the caller provides. [7](#0-6) [8](#0-7) 

---

### Impact Explanation

The `CompiledClassHash` v2 (Blake-based) stored in `stateless_compiled_class_hash_v2` is the authoritative hash used in:

1. **State diff output** — `class_hash_to_compiled_class_hash` in `ThinStateDiff` is populated from this table. A wrong value here produces a wrong state commitment. [9](#0-8) 

2. **OS proof verification** — `get_compiled_class_hash_v2` is called during execution to bind the class hash to its compiled artifact hash. The OS verifies this binding in the STARK proof. A silently overwritten hash causes the OS to verify against the wrong compiled class hash, producing an incorrect or unverifiable proof. [10](#0-9) 

3. **Compiler selection** — The v2 hash distinguishes Blake-compiled (v2) from Poseidon-compiled (v1) CASM. If the stored hash is overwritten with a v1 hash after migration, the blockifier will treat a migrated class as unmigrated, selecting the wrong compiled artifact for execution. [11](#0-10) 

This matches the impact scope: **Wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution** and **Wrong state, class hash, or storage value from blockifier/execution logic**.

---

### Likelihood Explanation

The broken invariant is latent but reachable through:

- **Re-sync / replay scenarios**: When a node re-processes historical blocks (e.g., `native_blockifier` replay), the `class_undeclared` guard checks the main state table, not the `stateless_compiled_class_hash_v2` table. A class that is already in `stateless_compiled_class_hash_v2` but whose main-state entry was reverted or is being re-applied will pass the `class_undeclared` check and trigger a silent overwrite.
- **Migration + re-declaration**: A class migrated from v1 to v2 hash can be silently reverted to v1 if `set_executable_class_hash_v2` is called again with the old hash.
- **No error signal**: Because `upsert` never errors, callers have no way to detect the collision. The broken contract means any future caller that adds a guard based on the documented "error on duplicate" behavior will not receive the expected signal.

---

### Recommendation

Replace `upsert` with `insert` in the implementation to enforce the documented write-once invariant:

```rust
impl<T: StorageTransaction<Mode = RW>> ClassHashStorageWriter for T {
    fn set_executable_class_hash_v2(
        self,
        class_hash: &ClassHash,
        executable_class_hash_v2: CompiledClassHash,
    ) -> StorageResult<Self> {
        let table = self.open_table(&self.tables().stateless_compiled_class_hash_v2)?;
        // Use insert (NO_OVERWRITE) to enforce the documented write-once contract.
        table.insert(self.txn(), class_hash, &executable_class_hash_v2)?;
        Ok(self)
    }
}
```

Callers that legitimately need to update an existing entry (e.g., migration from v1 to v2) should use a separate, explicitly named `update_executable_class_hash_v2` function that makes the overwrite intent explicit and validates the transition (e.g., asserts the old value is the expected v1 hash before overwriting). [2](#0-1) 

---

### Proof of Concept

```rust
// Demonstrates silent overwrite via set_executable_class_hash_v2.
// In a real scenario, this could be triggered by re-sync or migration replay.

let ((reader, mut writer), _temp_dir) = get_test_storage();

let class_hash = ClassHash(felt!("0xABCD"));
let original_v2_hash = CompiledClassHash(felt!("0x1111")); // Blake-based v2 hash
let attacker_hash   = CompiledClassHash(felt!("0x2222")); // Wrong/old v1 hash

// First write: legitimate initial population.
writer
    .begin_rw_txn().unwrap()
    .set_executable_class_hash_v2(&class_hash, original_v2_hash).unwrap()
    .commit().unwrap();

// Second write: silent overwrite — no error returned despite documented contract.
// Triggered by re-sync, replay, or migration path calling set_executable_class_hash_v2 again.
writer
    .begin_rw_txn().unwrap()
    .set_executable_class_hash_v2(&class_hash, attacker_hash).unwrap() // ← should error, doesn't
    .commit().unwrap();

// The stored hash is now wrong.
let stored = reader
    .begin_ro_txn().unwrap()
    .get_executable_class_hash_v2(&class_hash).unwrap();
assert_eq!(stored, Some(attacker_hash)); // original_v2_hash silently replaced

// Consequence: get_compiled_class_hash_v2 now returns attacker_hash,
// causing wrong state diff, wrong OS proof binding, and wrong compiler selection.
``` [12](#0-11) [2](#0-1)

### Citations

**File:** crates/apollo_storage/src/class_hash.rs (L1-6)
```rust
//! Interface for handling hashes of Starknet [classes (Cairo 1)](https://docs.rs/starknet_api/latest/starknet_api/state/struct.ContractClass.html).
//! This is a table separate from Papyrus storage; scope and version do not apply on it.
//! Use carefully, only within class manager code, which is responsible for maintaining this table.
//!
//! Import [`ClassHashStorageReader`] and [`ClassHashStorageWriter`] to read and write data related
//! to classes using a `StorageTxn`.
```

**File:** crates/apollo_storage/src/class_hash.rs (L35-41)
```rust
    /// Inserts the executable class hash corresponding to the given class hash.
    /// An error is returned if the class hash already exists.
    fn set_executable_class_hash_v2(
        self,
        class_hash: &ClassHash,
        executable_class_hash_v2: CompiledClassHash,
    ) -> StorageResult<Self>;
```

**File:** crates/apollo_storage/src/class_hash.rs (L54-63)
```rust
impl<T: StorageTransaction<Mode = RW>> ClassHashStorageWriter for T {
    fn set_executable_class_hash_v2(
        self,
        class_hash: &ClassHash,
        executable_class_hash_v2: CompiledClassHash,
    ) -> StorageResult<Self> {
        let table = self.open_table(&self.tables().stateless_compiled_class_hash_v2)?;
        table.upsert(self.txn(), class_hash, &executable_class_hash_v2)?;
        Ok(self)
    }
```

**File:** crates/apollo_storage/src/db/table_types/simple_table.rs (L84-113)
```rust
    fn upsert(
        &'env self,
        txn: &DbTransaction<'env, RW>,
        key: &Self::Key,
        value: &<Self::Value as ValueSerde>::Value,
    ) -> DbResult<()> {
        let data = <Self::Value>::serialize(value)?;
        let bin_key = key.serialize()?;
        txn.txn.put(&self.database, bin_key, data, WriteFlags::UPSERT)?;
        Ok(())
    }

    fn insert(
        &'env self,
        txn: &DbTransaction<'env, RW>,
        key: &Self::Key,
        value: &<Self::Value as ValueSerde>::Value,
    ) -> DbResult<()> {
        let data = <Self::Value>::serialize(value)?;
        let bin_key = key.serialize()?;
        txn.txn.put(&self.database, bin_key, data, WriteFlags::NO_OVERWRITE).map_err(|err| {
            match err {
                libmdbx::Error::KeyExist => {
                    DbError::KeyAlreadyExists(KeyAlreadyExistsError::new(self.name, key, value))
                }
                _ => err.into(),
            }
        })?;
        Ok(())
    }
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L74-79)
```rust
        if let Ok(Some(executable_class_hash_v2)) =
            self.classes.get_executable_class_hash_v2(class_hash)
        {
            // Class already exists.
            return Ok(ClassHashes { class_hash, executable_class_hash_v2 });
        }
```

**File:** crates/native_blockifier/src/storage.rs (L210-215)
```rust
        let mut append_txn = self.writer().begin_rw_txn()?;
        for (class_hash, casm_contract_class, compiled_class_hash_v2) in undeclared_casm_contracts {
            append_txn = append_txn.append_casm(&class_hash, &casm_contract_class)?;
            append_txn =
                append_txn.set_executable_class_hash_v2(&class_hash, compiled_class_hash_v2)?;
        }
```

**File:** crates/blockifier/src/state/compiled_class_hash_migration.rs (L16-36)
```rust
impl<S: StateReader> CompiledClassHashMigrationUpdater for CachedState<S> {
    // Sets the new compiled class hashes for the class hashes that need to be migrated.
    fn set_compiled_class_hash_migration(
        &mut self,
        class_hashes_to_migrate: &HashMap<ClassHash, CompiledClassHashV2ToV1>,
    ) -> StateResult<()> {
        for (class_hash, (compiled_class_hash_v2, compiled_class_hash_v1)) in
            class_hashes_to_migrate
        {
            // Sanity check: the compiled class hashes should not be equal.
            assert_ne!(
                compiled_class_hash_v1, compiled_class_hash_v2,
                "Classes for migration should hold v1 (Poseidon) hash in the state."
            );

            // TODO(Meshi): Consider panic here instead of returning an error.
            self.set_compiled_class_hash(*class_hash, *compiled_class_hash_v2)?;
        }

        Ok(())
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L87-118)
```text
// Migrates contract classes from v1 (Poseidon-based CASM hash) to v2 (Blake-based CASM hash).
// The class hashes are guessed, and should at least cover the non-migrated classes that
// will be executed by the block.
// Hint arguments:
// block_input - The block input containing the class hashes to migrate.
func migrate_classes_to_v2_casm_hash{
    poseidon_ptr: PoseidonBuiltin*, range_check_ptr, contract_class_changes: DictAccess*
}(n_classes: felt, block_context: BlockContext*) {
    alloc_locals;
    if (n_classes == 0) {
        return ();
    }
    // Guess the class hash and compiled class fact.
    local class_hash;
    local compiled_class_fact: CompiledClassFact*;
    %{ GetClassHashAndCompiledClassFact %}
    let compiled_class = compiled_class_fact.compiled_class;
    // Compute the full compiled class hash, both v1 and v2.
    // This hint enters a new scope that contains the bytecode segment structure of the class.
    %{ EnterScopeWithBytecodeSegmentStructure %}
    let (casm_hash_v1) = poseidon_compiled_class_hash(compiled_class, full_contract=TRUE);
    let (casm_hash_v2) = blake_compiled_class_hash(compiled_class, full_contract=TRUE);
    %{ vm_exit_scope() %}
    // Sanity check: verify the guessed v2 hash.
    assert compiled_class_fact.hash = casm_hash_v2;
    // Update the casm hash from v1 to v2.
    dict_update{dict_ptr=contract_class_changes}(
        key=class_hash, prev_value=casm_hash_v1, new_value=casm_hash_v2
    );
    migrate_classes_to_v2_casm_hash(n_classes=n_classes - 1, block_context=block_context);
    return ();
}
```

**File:** crates/apollo_storage/src/state/mod.rs (L895-906)
```rust
#[latency_histogram("storage_write_nonce_latency_seconds", false)]
fn write_compiled_class_hashes<'env>(
    compiled_class_hashes: &IndexMap<ClassHash, CompiledClassHash>,
    txn: &DbTransaction<'env, RW>,
    block_number: BlockNumber,
    compiled_class_hash_table: &'env CompiledClassHashTable<'env>,
) -> StorageResult<()> {
    for (class_hash, compiled_class_hash) in compiled_class_hashes {
        compiled_class_hash_table.insert(txn, &(*class_hash, block_number), compiled_class_hash)?;
    }
    Ok(())
}
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L162-188)
```rust
    fn get_compiled_class_hash_v2(
        &self,
        class_hash: ClassHash,
        compiled_class: &RunnableCompiledClass,
    ) -> StateResult<CompiledClassHash> {
        // First, try getting from class manager cache.
        match self.contract_class_manager.get_compiled_class_hash_v2(&class_hash) {
            Some(compiled_class_hash) => Ok(compiled_class_hash),
            None => {
                // Not in cache → fetch from state reader.
                let compiled_class_hash =
                    self.state_reader.get_compiled_class_hash_v2(class_hash, compiled_class)?;
                // Verify that the returned compiled_class_hash_v2 is not the default value.
                // default value is used to mark classes that are not declared or cairo0 classes.
                assert_ne!(
                    compiled_class_hash,
                    CompiledClassHash::default(),
                    "Default compiled class hash is for marking classes that are not declared or \
                     cairo0 classes. class_hash: {class_hash:?}"
                );
                // Store in cache.
                self.contract_class_manager
                    .set_compiled_class_hash_v2(class_hash, compiled_class_hash);

                Ok(compiled_class_hash)
            }
        }
```

**File:** crates/blockifier/src/bouncer.rs (L377-400)
```rust
    fn from_state<S: StateReader>(
        state_reader: &S,
        executed_class_hashes: &HashSet<ClassHash>,
        versioned_constants: &VersionedConstants,
    ) -> TransactionExecutionResult<Self> {
        if !versioned_constants.enable_casm_hash_migration {
            return Ok(Self::empty());
        }

        executed_class_hashes.iter().try_fold(Self::empty(), |mut migration_data, &class_hash| {
            if let Some((class_hash, casm_hash_v2_to_v1)) =
                should_migrate(state_reader, class_hash)?
            {
                // Add class hash mapping to the migration data.
                migration_data.class_hashes_to_migrate.insert(class_hash, casm_hash_v2_to_v1);

                // Accumulate the class's migration resources.
                let class = state_reader.get_compiled_class(class_hash)?;
                migration_data.resources +=
                    &class.estimate_compiled_class_hash_migration_resources();
            }
            Ok(migration_data)
        })
    }
```

**File:** crates/apollo_storage/src/class_hash_test.rs (L1-33)
```rust
use starknet_api::core::{ClassHash, CompiledClassHash};
use starknet_api::felt;

use crate::class_hash::{ClassHashStorageReader, ClassHashStorageWriter};
use crate::test_utils::get_test_storage;

#[test]
fn class_hash_storage() {
    let ((reader, mut writer), _temp_dir) = get_test_storage();

    // Non-existent entry.
    let class_hash = ClassHash(felt!("0x1234"));
    let executable_class_hash =
        reader.begin_ro_txn().unwrap().get_executable_class_hash_v2(&class_hash).unwrap();
    assert_eq!(executable_class_hash, None);

    // Insert an entry.
    let expected_executable_class_hash_v2 = CompiledClassHash(felt!("0x5678"));
    writer
        .begin_rw_txn()
        .unwrap()
        .set_executable_class_hash_v2(&class_hash, expected_executable_class_hash_v2)
        .unwrap()
        .commit()
        .unwrap();

    // Read the inserted entry.
    let executable_class_hash_v2 =
        reader.begin_ro_txn().unwrap().get_executable_class_hash_v2(&class_hash).unwrap();
    assert_eq!(executable_class_hash_v2, Some(expected_executable_class_hash_v2));
}


```
