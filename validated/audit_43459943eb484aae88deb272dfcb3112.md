No vulnerability found for this question.

The `dotty` prototype-pollution advisory concerns a JS/npm library that recursively sets nested object properties via dot-path strings, which can pollute `Object.prototype`. Searching this codebase for analogous patterns turned up only two categories of code, neither of which qualifies as an in-scope, transaction-reachable sequencer path:

1. Deployment/config tooling in Python (`deployments/sequencer/src/config/loaders.py`, `deployments/sequencer/src/config/overlay.py`, `deployments/sequencer/src/config/merger.py`) that merges YAML configs using dotted-key and recursive dict merge helpers such as `_set_nested_dotted_key` and `_merge_dict_strict`. [1](#0-0) [2](#0-1)  These operate on trusted, operator-authored YAML config files at deployment time, not on any data derived from a submitted transaction, contract call, declared class, or L1 message — this is explicitly excluded as "deployment"/"CLI" tooling per the scope rules.

2. The Starknet OS/blockifier state-aliasing mechanism (`crates/apollo_starknet_os_program/.../state/aliases.cairo`, `crates/blockifier/src/state/stateful_compression.rs`), which assigns numeric aliases to storage keys/contract addresses for compression. [3](#0-2)  This uses `StorageKey`/`ContractAddress`-indexed maps, not string dot-path property access, so it has no structural resemblance to a prototype-pollution bug class (no shared/prototype object, no recursive string-path traversal reachable by an attacker to write into unintended fields).

No code path was found where an unprivileged transaction sender, contract deployer, class declarer, or L1 message sender can supply a dot-path-like string that gets used to recursively set properties on a shared/prototype object within gateway validation, mempool, blockifier execution, syscalls, fee/bouncer accounting, state commitment, or OS re-execution. Given the lack of a reachable, in-scope analog, no valid finding exists for this report.

### Citations

**File:** deployments/sequencer/src/config/loaders.py (L248-262)
```python
    @staticmethod
    def _set_nested_dotted_key(data: dict, dotted_key: str, value: Any) -> None:
        """Set value in nested dict using dotted key notation, creating structure if needed.

        Examples:
            _set_nested_dotted_key({}, 'a.b.c', 123) -> {'a': {'b': {'c': 123}}}
            _set_nested_dotted_key({'a': {'x': 1}}, 'a.b.c', 123) -> {'a': {'x': 1, 'b': {'c': 123}}}
        """
        keys = dotted_key.split(".")
        current = data
        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value
```

**File:** deployments/sequencer/src/config/overlay.py (L359-406)
```python
def _merge_dict_strict(
    layout: dict,
    overlay: dict,
    path: str = "",
    source: str = _UNKNOWN_SOURCE,
    schema_model: Optional[Any] = None,
    parent_is_dict_field: bool = False,
) -> dict:
    """Recursively merge overlay dict into layout dict with schema-based validation.

    Keys are validated against the Pydantic schema. If a key exists in the schema,
    it can be added even if not present in the layout. This allows overlays to use
    all schema-valid fields without requiring them to be in the layout.

    Args:
        layout: The base layout dictionary.
        overlay: The overlay dictionary to merge.
        path: The current path in the dictionary hierarchy (for error messages).
        source: The source identifier of the overlay file.
        schema_model: Optional Pydantic model for nested validation.
        parent_is_dict_field: Whether the parent field is a dict type (StrDict/AnyDict).

    Returns:
        A new dictionary with the merged values.

    Raises:
        ValueError: If overlay tries to add a key not in the schema.
    """
    layout_copy = deepcopy(layout)

    for key, val in overlay.items():
        current_path = f"{path}.{key}" if path else key

        # If parent is a dict field, skip all validation
        if parent_is_dict_field:
            existing = layout_copy.get(key, {})
            layout_copy[key] = _merge_dict_field(existing, val, current_path, source)
            continue

        # Get the schema model for this key
        if schema_model is None:
            parent_path = path.rsplit(".", 1)[0] if "." in path else ""
            parent_model = _get_schema_model_for_path(parent_path) if parent_path else ServiceConfig
        else:
            parent_model = schema_model

        # Validate key exists in schema (or layout)
        validate_key_exists(layout, key, current_path, source, schema_model=parent_model)
```

**File:** crates/blockifier/src/state/stateful_compression.rs (L107-142)
```rust
/// Updates the alias contract with the new keys.
struct AliasUpdater<'a, S: State> {
    state: &'a mut S,
    is_alias_inserted: bool,
    next_free_alias: Option<Alias>,
    alias_contract_address: ContractAddress,
}

impl<'a, S: State> AliasUpdater<'a, S> {
    fn new(state: &'a mut S, alias_contract_address: ContractAddress) -> StateResult<Self> {
        let stored_counter =
            state.get_storage_at(alias_contract_address, ALIAS_COUNTER_STORAGE_KEY)?;
        Ok(Self {
            state,
            is_alias_inserted: false,
            next_free_alias: if stored_counter == Felt::ZERO { None } else { Some(stored_counter) },
            alias_contract_address,
        })
    }

    fn set_alias_in_storage(&mut self, alias_key: AliasKey, alias: Alias) -> StateResult<()> {
        self.state.set_storage_at(self.alias_contract_address, alias_key, alias)
    }

    /// Inserts the alias key to the updates if it's not already aliased.
    fn insert_alias(&mut self, alias_key: &AliasKey) -> StateResult<()> {
        if alias_key.0 >= MIN_VALUE_FOR_ALIAS_ALLOC
            && self.state.get_storage_at(self.alias_contract_address, *alias_key)? == Felt::ZERO
        {
            let alias_to_allocate = self.next_free_alias.unwrap_or(INITIAL_AVAILABLE_ALIAS);
            self.set_alias_in_storage(*alias_key, alias_to_allocate)?;
            self.is_alias_inserted = true;
            self.next_free_alias = Some(alias_to_allocate + Felt::ONE);
        }
        Ok(())
    }
```
