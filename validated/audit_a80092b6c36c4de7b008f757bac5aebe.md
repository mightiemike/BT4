[1](#0-0)

### Citations

**File:** stackslib/src/chainstate/stacks/index/trie.rs (L836-854)
```rust
    pub fn get_trie_ancestor_hashes_bytes<T: MarfTrieId>(
        storage: &mut TrieStorageConnection<T>,
    ) -> Result<Vec<TrieHash>, Error> {
        let (cur_block_header, cur_block_id) = storage.get_cur_block_and_id();
        if let Some(cached_ancestor_hashes_bytes) =
            storage.check_cached_ancestor_hashes_bytes(&cur_block_header)
        {
            Ok(cached_ancestor_hashes_bytes)
        } else {
            let result = Trie::inner_get_trie_ancestor_hashes_bytes(storage);
            if let Ok(ref result) = result {
                storage.set_cached_ancestor_hashes_bytes(&cur_block_header, result.clone());
            }

            // restore
            storage.open_block_maybe_id(&cur_block_header, cur_block_id)?;
            result
        }
    }
```
