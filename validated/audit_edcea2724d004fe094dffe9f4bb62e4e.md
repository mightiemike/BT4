[1](#0-0) [2](#0-1)

### Citations

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
