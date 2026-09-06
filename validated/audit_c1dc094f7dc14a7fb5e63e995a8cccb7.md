Confirmed the reachability analysis is complete: `add_shadow_block` and `process_shadow_block` are only defined/invoked in `stackslib/src/chainstate/nakamoto/shadow.rs` (trusted tooling entrypoint) and `stackslib/src/chainstate/nakamoto/tests/node.rs` (test-only harness `make_shadow_tenure`). Neither is reachable from any network/RPC ingress path.

#No vulnerability found for this question.

The shadow-block bypass in `NakamotoBlockHeader::verify_signer_signatures` (`stackslib/src/chainstate/nakamoto/mod.rs:1111-1113`) is real code, but it is unreachable by an unprivileged attacker through any consensus-relevant ingress point:

- **Network/P2P path**: `Relayer::process_new_nakamoto_block_ext` explicitly drops any incoming block with `is_shadow_block() == true` before it ever reaches `accept_block`/`verify_signer_signatures`: [1](#0-0) 
- **RPC block-proposal endpoint**: `RPCBlockProposalRequestHandler::try_parse_request` rejects any submitted block with the shadow bit set outright: [2](#0-1) 
- **The only path that reaches `accept_block` with `is_shadow_block()==true`** is `process_shadow_block`, which requires the block to already be pre-inserted into the staging DB via `NakamotoStagingBlocksTx::add_shadow_block`, and is explicitly documented as "DO NOT RUN ON A RUNNING NODE (unless you're testing)": [3](#0-2) . This is invoked only by trusted SIP-driven tooling (`NakamotoBlockBuilder::make_shadow_tenure`) or test harnesses, not by any code path an unprivileged network participant can trigger.
- In `NakamotoChainState::accept_block`, blocks that are shadow blocks are handled with a `panic!` on validation failure and are assumed already present/valid — this branch is dead weight for any block arriving through normal miner/relay/RPC ingestion, since those paths filter shadow blocks out before `accept_block` is ever called: [4](#0-3) 

Since the attacker (unprivileged participant able only to broadcast blocks/commits/keys via P2P/RPC) has no way to get a shadow-flagged block routed to `accept_block`/`verify_signer_signatures`, the SIGNING equality (`total_weight_signed >= threshold`) is never actually bypassed for any block reachable by such an attacker — the bypass only fires for blocks that a node operator/tooling process has already trust-inserted via a SIP-mandated schema update, which is a privileged operation explicitly out of scope per the rules (no privileged role, no node-operator/admin action).

### Citations

**File:** stackslib/src/net/relay.rs (L923-927)
```rust
        if block.is_shadow_block() {
            // drop, since we can get these from ourselves when downloading a tenure that ends in
            // a shadow block.
            return Ok(BlockAcceptResponse::AlreadyStored);
        }
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L1171-1175)
```rust
        if block_proposal.block.is_shadow_block() {
            return Err(Error::DecodeError(
                "Shadow blocks cannot be submitted for validation".to_string(),
            ));
        }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L881-891)
```rust
/// DO NOT RUN ON A RUNNING NODE (unless you're testing).
///
/// Insert and process a shadow block into the Stacks chainstate.
pub fn process_shadow_block(
    chain_state: &mut StacksChainState,
    sort_db: &mut SortitionDB,
    shadow_block: NakamotoBlock,
) -> Result<(), ChainstateError> {
    let tx = chain_state.staging_db_tx_begin()?;
    tx.add_shadow_block(&shadow_block)?;
    tx.commit()?;
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2860-2879)
```rust
        if block.is_shadow_block() {
            // this block is already present in the staging DB, so just perform some prefunctory
            // validation (since they're constructed a priori to be valid)
            Self::validate_shadow_nakamoto_block_burnchain(
                staging_db_tx.conn(),
                db_handle,
                expected_burn_opt,
                block,
                config.mainnet,
                config.chain_id,
            )
            .unwrap_or_else(|e| {
                error!("Unacceptable shadow Nakamoto block";
                    "stacks_block_id" => %block_id,
                    "error" => ?e
                );
                panic!("Unacceptable shadow Nakamoto block");
            });
            return Ok(false);
        }
```
