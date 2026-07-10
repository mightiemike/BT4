### Title
Temporary Fork Promotion Enables Fraudulent Transaction Verification - (File: `contract/src/lib.rs`)

---

### Summary

The `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` functions evaluate mainchain membership at the instant of the call with no temporal commitment or challenge period. Because `submit_block_header_inner` immediately promotes a fork to the canonical chain the moment its chainwork exceeds the current tip, a malicious relayer can submit a fork chain to trigger a reorg, call `verify_transaction_inclusion` to obtain a fraudulent `true` result for a transaction that never existed on the real Bitcoin chain, and then allow the honest chain to reorg back — all without any long-term commitment.

---

### Finding Description

**Root cause — immediate, unconditional fork promotion:**

In `submit_block_header_inner`, when a submitted fork block's accumulated `chain_work` exceeds the current mainchain tip's `chain_work`, `reorg_chain` is called immediately with no delay, no challenge window, and no minimum depth requirement: [1](#0-0) 

`reorg_chain` then rewrites `mainchain_height_to_header` and `mainchain_header_to_height` in-place, making the fork blocks the new canonical chain: [2](#0-1) 

**Root cause — point-in-time mainchain membership check:**

`verify_transaction_inclusion` determines whether a transaction is confirmed solely by looking up `tx_block_blockhash` in `mainchain_header_to_height` at call time: [3](#0-2) 

There is no snapshot, no minimum age requirement for the block's mainchain tenure, and no protection against a block that was just promoted via a reorg seconds ago.

**Attack path:**

1. Attacker stakes to become a trusted relayer (the `trusted_relayer` macro from `omni_utils` is an economic barrier, not a privileged-role gate).
2. Attacker pre-mines a private fork chain of length `C` (where `C` equals the confirmation count the target recipient contract requires), accumulating chainwork exceeding the current mainchain tip.
3. Attacker calls `submit_blocks` with the fork chain. `reorg_chain` fires immediately, writing the attacker's fork blocks into `mainchain_height_to_header` and `mainchain_header_to_height`.
4. Attacker (or a colluding recipient contract in the same NEAR block) calls `verify_transaction_inclusion` with a fabricated `tx_id` and a Merkle proof crafted against the attacker-controlled `merkle_root` in the fork block. The function returns `true`.
5. The honest relayer eventually submits real Bitcoin blocks; the real chain reorgs the attacker's fork out. The attacker's fork blocks are removed from the mainchain maps. But the fraudulent verification result has already been consumed. [4](#0-3) 

---

### Impact Explanation

A recipient contract that calls `verify_transaction_inclusion` (or `verify_transaction_inclusion_v2`) to gate an asset release, a bridge mint, or any other irreversible on-chain action will receive `true` for a transaction that was never confirmed on the real Bitcoin chain. The corrupted state is the `mainchain_header_to_height` mapping, which is temporarily rewritten to include attacker-controlled blocks, causing the proof result to be forged. [5](#0-4) 

---

### Likelihood Explanation

For **Bitcoin mainnet** with `skip_pow_verification = false`, each fork block requires valid SHA-256d PoW at the current network difficulty. The attacker must mine `C` blocks privately before the honest chain advances, which requires a meaningful fraction of the network hashrate. This is expensive but not impossible for a well-resourced mining pool or a 51%-attack scenario — exactly the threat model a light client is supposed to defend against.

For **Dogecoin** (AuxPoW merged mining) and **Litecoin** (Scrypt), difficulty is lower and the cost is substantially reduced.

The `verify_transaction_inclusion_v2` path adds a coinbase Merkle proof check but does not change the mainchain-membership or temporal-commitment logic, so it is equally affected. [6](#0-5) 

---

### Recommendation

Introduce a **minimum mainchain tenure** before a block is eligible for verification. Concretely:

- Record the NEAR block height (or timestamp) at which each `ExtendedHeader` was first written into `mainchain_height_to_header` as part of a reorg.
- In `verify_transaction_inclusion`, reject any `tx_block_blockhash` whose mainchain tenure is shorter than a configurable minimum (e.g., equivalent to the expected time for `confirmations` honest Bitcoin blocks to arrive).

This mirrors the recommendation in the original report: weight earlier commitment more heavily, making the temporary-manipulation attack more expensive by requiring the attacker to hold the reorged chain for a meaningful period before the verification can be exploited.

---

### Proof of Concept

```
// NEAR pseudocode — attacker controls a relayer account

// Step 1: pre-mine a private fork of depth 6 off block N-1
// fork_blocks[0..5] each have valid PoW and a fabricated merkle_root
// containing fake_tx_id at index 0

// Step 2: submit the fork — triggers immediate reorg
contract.submit_blocks(fork_blocks);
// mainchain_height_to_header[N..N+5] now point to attacker's fork blocks

// Step 3: call verify_transaction_inclusion in the same NEAR block
// (or via a cross-contract call from the recipient contract)
let confirmed = contract.verify_transaction_inclusion(ProofArgs {
    tx_id: fake_tx_id,
    tx_block_blockhash: fork_blocks[0].block_hash(),
    tx_index: 0,
    merkle_proof: crafted_proof,  // valid against attacker's merkle_root
    confirmations: 6,
});
// confirmed == true  ← fraudulent result

// Step 4: honest relayer submits real Bitcoin blocks → reorg back
// attacker's fork is evicted from mainchain maps
// but the recipient contract already acted on `confirmed == true`
```

The broken invariant is that `mainchain_header_to_height` membership is treated as a durable proof of Bitcoin confirmation, when it is actually an instantaneous, reorg-reversible snapshot that can be temporarily written by any sufficiently resourced relayer. [7](#0-6) [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L347-369)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/src/lib.rs (L531-568)
```rust
    fn submit_block_header_inner(
        &mut self,
        current_header: ExtendedHeader,
        prev_block_header: &ExtendedHeader,
    ) {
        // Main chain submission
        if prev_block_header.block_hash == self.mainchain_tip_blockhash {
            // Probably we should check if it is not in a mainchain?
            // chainwork > highScore
            log!("Block {}: saving to mainchain", current_header.block_hash);
            // Validate chain
            assert_eq!(
                self.mainchain_tip_blockhash,
                current_header.block_header.prev_block_hash
            );

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
    }
```

**File:** contract/src/lib.rs (L616-646)
```rust
        while !self
            .mainchain_header_to_height
            .contains_key(&fork_header_cursor.block_hash)
        {
            let prev_block_hash = fork_header_cursor.block_header.prev_block_hash;
            let current_block_hash = fork_header_cursor.block_hash;
            let current_height = fork_header_cursor.block_height;

            // Inserting the fork block into the main chain, if some mainchain block is occupying
            // this height let's save its hashcode
            let main_chain_block = self
                .mainchain_height_to_header
                .insert(&current_height, &current_block_hash);
            self.mainchain_header_to_height
                .insert(&current_block_hash, &current_height);

            // If we found a mainchain block at the current height than remove this block from the
            // header pool and from the header -> height map
            if let Some(current_main_chain_blockhash) = main_chain_block {
                self.remove_block_header(&current_main_chain_blockhash);
            }

            // Switch iterator cursor to the previous block in fork
            fork_header_cursor = self
                .headers_pool
                .get(&prev_block_hash)
                .unwrap_or_else(|| env::panic_str("previous fork block should be there"));
        }

        // Updating tip of the new main chain
        self.mainchain_tip_blockhash = fork_tip_hash;
```

**File:** contract/src/lib.rs (L649-656)
```rust
    /// Stores parsed block header and meta information
    fn store_block_header(&mut self, header: &ExtendedHeader) {
        self.mainchain_height_to_header
            .insert(&header.block_height, &header.block_hash);
        self.mainchain_header_to_height
            .insert(&header.block_hash, &header.block_height);
        self.headers_pool.insert(&header.block_hash, header);
    }
```
