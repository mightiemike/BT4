No vulnerability found for this question.

**Reasoning:**

The premise that an "unprivileged encrypted-transaction submitter" can control `decryption_key` is incorrect. `PerBlockDecryptionKey::decryption_key` and `PerBlockDecryptionKeyV2::decryption_key` are only ever written via `on_new_block` / `on_new_block_v2`, both of which are `public(friend)` functions restricted to `aptos_framework::block` and `aptos_framework::reconfiguration_with_dkg`, and both immediately call `system_addresses::assert_vm(vm)` before touching the resource [1](#0-0) [2](#0-1) .

These functions are only invoked from `block_prologue_ext_v2`/`block_prologue_ext_v3` inside `block.move`, which is itself only callable as part of the VM-generated block prologue transaction (`vm: signer`), not by any user or unprivileged transaction [3](#0-2) [4](#0-3) .

The actual byte content of `decryption_key` originates from the consensus-side threshold-decryption pipeline (`BlockTxnDecryptionKey::from_secret_shared_key`, serialized from a `SecretSharedKey`), not from arbitrary user-supplied bytes [5](#0-4) . There is no bytecode/API/transaction path where a normal, unprivileged account can set this field to an attacker-chosen, arbitrarily large `vector<u8>`. Since the write set for these resources is entirely bounded by validator/DKG-produced key material (deterministically sized by the cryptographic scheme) and never influenced by external submitter-controlled input, there is no state-integrity or proof-desynchronization vector here that meets the "unprivileged input" scope requirement.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/decryption.move (L90-105)
```text
    /// Invoked in block prologues to update the block decryption key.
    public(friend) fun on_new_block(
        vm: &signer,
        epoch: u64,
        round: u64,
        decryption_key_for_new_block: Option<vector<u8>>
    ) acquires PerBlockDecryptionKey {
        system_addresses::assert_vm(vm);
        if (exists<PerBlockDecryptionKey>(@aptos_framework)) {
            let decryption_key =
                borrow_global_mut<PerBlockDecryptionKey>(@aptos_framework);
            decryption_key.epoch = epoch;
            decryption_key.round = round;
            decryption_key.decryption_key = decryption_key_for_new_block;
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/decryption.move (L110-128)
```text
    public(friend) fun on_new_block_v2(
        vm: &signer,
        epoch: u64,
        round: u64,
        decryption_key_for_new_block: Option<vector<u8>>,
        decryption_round: Option<u64>
    ) acquires PerBlockDecryptionKeyV2 {
        system_addresses::assert_vm(vm);
        if (exists<PerBlockDecryptionKeyV2>(@aptos_framework)) {
            let r = borrow_global_mut<PerBlockDecryptionKeyV2>(@aptos_framework);
            r.epoch = epoch;
            r.block_round = round;
            r.decryption_key = decryption_key_for_new_block;
            r.decryption_round = decryption_round;
            if (option::is_some(&decryption_round)) {
                r.next_decryption_round = *option::borrow(&decryption_round) + 1;
            }
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/block.move (L271-301)
```text
    fun block_prologue_ext_v2(
        vm: signer,
        hash: address,
        epoch: u64,
        round: u64,
        proposer: address,
        failed_proposer_indices: vector<u64>,
        previous_block_votes_bitvec: vector<u8>,
        timestamp: u64,
        randomness_seed: Option<vector<u8>>,
        decryption_key: Option<vector<u8>>
    ) acquires BlockResource, CommitHistory {
        let epoch_interval =
            block_prologue_common(
                &vm,
                hash,
                epoch,
                round,
                proposer,
                failed_proposer_indices,
                previous_block_votes_bitvec,
                timestamp
            );
        randomness::on_new_block(&vm, epoch, round, randomness_seed);
        decryption::on_new_block(&vm, epoch, round, decryption_key);

        if (timestamp - reconfiguration::last_reconfiguration_time() >= epoch_interval) {
            reconfiguration_with_dkg::try_start_with_chunky_dkg();
            reconfiguration_with_dkg::try_advance_reconfig();
        };
    }
```

**File:** aptos-move/framework/aptos-framework/sources/block.move (L306-337)
```text
    fun block_prologue_ext_v3(
        vm: signer,
        hash: address,
        epoch: u64,
        round: u64,
        proposer: address,
        failed_proposer_indices: vector<u64>,
        previous_block_votes_bitvec: vector<u8>,
        timestamp: u64,
        randomness_seed: Option<vector<u8>>,
        decryption_key: Option<vector<u8>>,
        decryption_round: Option<u64>
    ) acquires BlockResource, CommitHistory {
        let epoch_interval =
            block_prologue_common(
                &vm,
                hash,
                epoch,
                round,
                proposer,
                failed_proposer_indices,
                previous_block_votes_bitvec,
                timestamp
            );
        randomness::on_new_block(&vm, epoch, round, randomness_seed);
        decryption::on_new_block_v2(&vm, epoch, round, decryption_key, decryption_round);

        if (timestamp - reconfiguration::last_reconfiguration_time() >= epoch_interval) {
            reconfiguration_with_dkg::try_start_with_chunky_dkg();
            reconfiguration_with_dkg::try_advance_reconfig();
        };
    }
```

**File:** types/src/decryption.rs (L50-58)
```rust
    pub fn from_secret_shared_key(key: &SecretSharedKey) -> anyhow::Result<Self> {
        Ok(Self::new(
            DecKeyMetadata {
                epoch: key.metadata.epoch,
                round: key.metadata.round,
            },
            bcs::to_bytes(&key.key).context("SecretSharedKey serialization")?,
        ))
    }
```
