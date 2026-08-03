[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/vm-genesis/src/genesis_context.rs (L34-48)
```rust
    pub(crate) fn add_module(&mut self, module_id: &ModuleId, blob: &[u8]) {
        self.state_data
            .insert(StateKey::module_id(module_id), blob.to_vec());
    }

    pub(crate) fn add_module_write_ops(
        &mut self,
        module_write_ops: BTreeMap<StateKey, ModuleWrite<WriteOp>>,
    ) {
        for (state_key, write) in module_write_ops {
            assert!(state_key.is_module_path());
            let bytes = assert_some!(write.write_op().bytes(), "Modules cannot be deleted");
            self.state_data.insert(state_key, bytes.to_vec());
        }
    }
```

**File:** aptos-move/vm-genesis/src/lib.rs (L6-17)
```rust
mod genesis_context;

use crate::genesis_context::GenesisStateView;
use aptos_crypto::{
    bls12381,
    ed25519::{Ed25519PrivateKey, Ed25519PublicKey},
    HashValue, PrivateKey, Uniform,
};
use aptos_gas_schedule::{
    AptosGasParameters, InitialGasSchedule, ToOnChainGasSchedule, LATEST_GAS_FEATURE_VERSION,
};
use aptos_release_bundle::{ReleaseBundle, ReleasePackage};
```
