### Title
Absence of a chain-identifying field in `SignedTransaction` allows cross-chain replay of pre-fork transactions after a NEAR hard fork - (File: chain/chain/src/store/utils.rs)

### Summary
NEAR's transaction "freshness"/replay protection binds a `SignedTransaction` only to a `block_hash` from the signer's own recent history, verified by walking local chain ancestry (`transaction_validity_period` blocks). There is no chain-identifying field (e.g. `chain_id` or genesis hash) inside the signed `Transaction` payload itself. If the network undergoes a hard fork/contentious split that produces two chains sharing identical history up to the fork point, a transaction signed before the split remains a valid ancestor-chain reference on both resulting chains and can be broadcast and accepted on both, similarly to the reported `IncentivizedMockImplementation` issue where a hardcoded `SOURCE_IDENTIFIER` (rather than something like `chainId()`) allowed a signed message to be replayed on the sibling chain after a fork.

### Finding Description
A `SignedTransaction`'s replay protection is implemented purely via `block_hash` + `nonce`, not any network/chain identifier:
- `Transaction` fields (`signer_id`, `public_key`, `nonce`, `receiver_id`, `block_hash`, `actions`) contain no `chain_id`/genesis-hash binding [1](#0-0) .
- Validity is checked with `check_transaction_validity_period`, which loads the header for `base_block_hash` and requires it be an ancestor of `prev_block_header` within `transaction_validity_period` blocks: [2](#0-1) .
- The ancestry check `validity_period_validate_is_ancestor` resolves ancestry purely from the local chain's stored block-height index / header-walk, with no chain identifier comparison at all: [3](#0-2) .
- This same check is used both at RPC ingestion (`rpc_handler.rs`) and at chunk production/validation time: [4](#0-3) [5](#0-4) .

The only per-network identifier NEAR exposes is `chain_id`, but it is a purely informational/host-function value (`env::chain_id()`, from genesis config) that is never included in, or checked against, the signed transaction bytes or its validity check: [6](#0-5) .

Consequently, if a contentious hard fork splits the network into chain A and chain B that share identical block history up to the fork block, a transaction referencing a `block_hash` from that shared pre-fork history is a valid ancestor on both post-fork chains. An unprivileged holder of any previously-signed, still-in-the-validity-window transaction (or a copy of a broadcast-but-unconfirmed transaction) can submit it to both chain A's and chain B's RPC nodes, and both will independently accept and execute it, because neither chain's node checks any chain-unique identifier distinguishing the two post-fork branches — only shared ancestor history within `transaction_validity_period`.

### Impact Explanation
This is a direct cross-chain replay of a single signed transaction (transfer, function call, stake, etc.), causing the same signer-authorized action (e.g. a token transfer) to execute on both post-fork chains from one signature. This constitutes unauthorized value movement/duplication of an action the user only intended to happen once (on one chain), analogous to the classic ETH/ETC replay problem that EIP-155's `chainId` was designed to prevent. Because both post-fork chains process it as fully valid, this is not a "no-impact" issue — it can cause real double-execution of a fund transfer or a staking/withdraw action across the two resulting NEAR chains.

### Likelihood Explanation
Likelihood is Low-to-Medium, matching the severity classification in the original report: NEAR's finality/protocol-upgrade mechanism (validator voting on `PROTOCOL_VERSION`) makes a genuine contentious chain split rare compared to PoW chains, but is not architecturally impossible (e.g., a controversial governance dispute or an emergency divergent patch deployed by a subset of validators). Given the impact (unauthorized duplicate execution of a financial transaction) and the fact that the root cause — no chain-binding data in the signed payload — is a concrete, verifiable design gap, this is assessed as Medium, consistent with the referenced report's own severity rating for the identical bug class.

### Recommendation
Bind `Transaction` signing/validity to a chain-unique, fork-sensitive identifier in addition to `block_hash`:
- Include the network's `chain_id` (from genesis config) as part of the signed `Transaction` payload (similar to Wormhole's `chainId()`-based approach cited as the "safe" comparator in the original report), so transactions signed for one branch cannot be replayed on the other.
- Alternatively/additionally, require any hard-fork protocol upgrade to mandate a `chain_id` (or equivalent epoch/fork marker) bump for the diverging branch, and have `check_transaction_validity_period`/RPC ingestion reject transactions whose payload was not signed for the node's current `chain_id`.

### Proof of Concept
Conceptual PoC (cannot be executed without a live two-branch network, but derivable directly from code paths):
1. Sign `tx` with `block_hash = B` (a block height `h`, `h <= head.height - transaction_validity_period`).
2. At height `h + k` (k < transaction_validity_period), the network splits into chain A and chain B, both of which still contain block `B` in their canonical ancestry.
3. Submit `tx` via JSON-RPC to a chain-A node: `check_transaction_validity_period` succeeds because `B` is an ancestor of chain A's head within the window — [4](#0-3) . The transaction executes on chain A.
4. Submit the identical `tx` bytes via JSON-RPC to a chain-B node: the same check passes independently, since `B` is equally an ancestor of chain B's head and nothing in the check inspects a chain-unique identifier — [2](#0-1) . The transaction executes again on chain B.
5. Result: the single signature authorizes the action twice, once per chain, with no user re-authorization — the cross-chain replay outcome.

### Citations

**File:** core/primitives/src/transaction.rs (L1-1)
```rust
use crate::action::UniversalStateInitAction;
```

**File:** chain/chain/src/store/utils.rs (L56-75)
```rust
pub fn check_transaction_validity_period(
    chain_store: &ChainStoreAdapter,
    prev_block_header: &BlockHeader,
    base_block_hash: &CryptoHash,
    transaction_validity_period: BlockHeightDelta,
) -> Result<(), InvalidTxError> {
    let base_header =
        chain_store.get_block_header(base_block_hash).map_err(|_| InvalidTxError::Expired)?;

    metrics::CHAIN_VALIDITY_PERIOD_CHECK_DELAY
        .observe(prev_block_header.height().saturating_sub(base_header.height()) as f64);

    // First check the distance between blocks
    if prev_block_header.height() > base_header.height() + transaction_validity_period {
        return Err(InvalidTxError::Expired);
    }

    // Then check if there is a path between the blocks (`base` is an ancestor of `prev`)
    validity_period_validate_is_ancestor(&base_header, prev_block_header, chain_store)
}
```

**File:** chain/chain/src/store/utils.rs (L130-177)
```rust
fn validity_period_validate_is_ancestor(
    base_header: &BlockHeader,
    prev_block_header: &BlockHeader,
    chain_store: &ChainStoreAdapter,
) -> Result<(), InvalidTxError> {
    let base_height = base_header.height();
    let prev_height = prev_block_header.height();
    let base_block_hash = base_header.hash();

    // Base can't be an ancestor of prev if its height is bigger
    if base_height > prev_height {
        return Err(InvalidTxError::InvalidChain);
    }

    // if both are on the canonical chain, comparing height is sufficient
    // we special case this because it is expected that this scenario will happen in most cases.
    if let Ok(base_block_hash_by_height) = chain_store.get_block_hash_by_height(base_height) {
        if &base_block_hash_by_height == base_block_hash {
            if let Ok(prev_hash) = chain_store.get_block_hash_by_height(prev_height) {
                if &prev_hash == prev_block_header.hash() {
                    return Ok(());
                }
            }
        }
    }

    // if the base block height is smaller than `last_final_height` we only need to check
    // whether the base block is the same as the one with that height on the canonical fork.
    // Otherwise we walk back the chain to check whether base block is on the same chain.
    let last_final_height = chain_store
        .get_block_height(prev_block_header.last_final_block())
        .map_err(|_| InvalidTxError::InvalidChain)?;

    if last_final_height >= base_height {
        let base_block_hash_by_height = chain_store
            .get_block_hash_by_height(base_height)
            .map_err(|_| InvalidTxError::InvalidChain)?;
        if &base_block_hash_by_height == base_block_hash {
            Ok(())
        } else {
            Err(InvalidTxError::InvalidChain)
        }
    } else {
        let header =
            get_block_header_on_chain_by_height(chain_store, prev_block_header.hash(), base_height)
                .map_err(|_| InvalidTxError::InvalidChain)?;
        if header.hash() == base_block_hash { Ok(()) } else { Err(InvalidTxError::InvalidChain) }
    }
```

**File:** chain/client/src/rpc_handler.rs (L167-175)
```rust
        if let Err(e) = check_transaction_validity_period(
            &self.chain_store,
            &cur_block_header,
            signed_tx.transaction.block_hash(),
            self.config.transaction_validity_period,
        ) {
            tracing::debug!(target: "client", ?signed_tx, "invalid tx: expired or from a different fork");
            return Ok(ProcessTxResponse::InvalidTx(e));
        }
```

**File:** chain/chain/src/store/mod.rs (L478-491)
```rust
    /// For a given transaction, it expires if the block that the chunk points to is more than `validity_period`
    /// ahead of the block that has `base_block_hash`.
    pub fn check_transaction_validity_period(
        &self,
        prev_block_header: &BlockHeader,
        base_block_hash: &CryptoHash,
    ) -> Result<(), InvalidTxError> {
        check_transaction_validity_period(
            &self.store,
            prev_block_header,
            base_block_hash,
            self.transaction_validity_period,
        )
    }
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L571-589)
```rust
/// Saves the chain ID of the current chain into the register.
///
/// # Errors
///
/// If the registers exceed the memory limit returns `MemoryAccessViolation`.
///
/// # Cost
///
/// `base + write_register_base + write_register_byte * num_bytes`
pub fn chain_id(ctx: &mut Ctx, _memory: &mut [u8], register_id: u64) -> Result<()> {
    ctx.result_state.gas_counter.pay_base(base)?;
    let chain_id = ctx.ext.chain_id();
    ctx.registers.set(
        &mut ctx.result_state.gas_counter,
        &ctx.config.limit_config,
        register_id,
        chain_id.as_bytes(),
    )
}
```
