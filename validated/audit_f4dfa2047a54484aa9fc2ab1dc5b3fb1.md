[1](#0-0)

### Citations

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L524-532)
```rust
    pub fn get_ongoing_tenure<SDBI: StacksDBIndexed>(
        headers_conn: &mut SDBI,
        tip_block_id: &StacksBlockId,
    ) -> Result<Option<NakamotoTenureEvent>, ChainstateError> {
        let Some(tenure_id) = headers_conn.get_ongoing_tenure_id(tip_block_id)? else {
            return Ok(None);
        };
        Self::get_nakamoto_tenure_change(headers_conn.sqlite(), &tenure_id)
    }
```
