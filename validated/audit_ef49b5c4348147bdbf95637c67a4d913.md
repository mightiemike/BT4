[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/session_id.rs (L107-109)
```rust
    pub fn genesis(id: HashValue) -> Self {
        Self::Genesis { id }
    }
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/session_id.rs (L208-217)
```rust
    // This is used in `monotonically_increasing_number` native function. Every call to the native function
    // will output a monotonically increasing number.
    // monotonically_increasing_number (128 bits) = 0 (8 bits -- Reserved for future use) || timestamp (64 bits) || transaction_index_inside_block (32 bits) || session_counter_inside_transaction (8 bits) || local_counter_inside_session (16 bits)
    // This function is used to obtain `session_counter_inside_transaction`.
    // The sessions here are organized in the increasing order in which they are created. Eg: Prologue < Txn < RunOnAbort < Epilogue.
    // When introducing new session types, please check the order in which the sessions are created during a transaction execution and assign a number here accordingly.
    pub fn session_counter(&self) -> u8 {
        match self {
            Self::Genesis { .. } => 0,

```

**File:** aptos-move/aptos-vm/src/move_vm_ext/vm.rs (L84-100)
```rust
impl GenesisMoveVm {
    /// Returns a new genesis session.
    pub fn new_genesis_session<'r, R: AptosMoveResolver>(
        &self,
        resolver: &'r R,
        genesis_id: HashValue,
    ) -> SessionExt<'r, R> {
        let session_id = SessionId::genesis(genesis_id);
        SessionExt::new(
            session_id,
            self.chain_id,
            &self.features,
            &self.vm_config,
            None,
            resolver,
        )
    }
```
