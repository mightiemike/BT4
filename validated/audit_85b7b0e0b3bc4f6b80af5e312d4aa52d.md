### Title
`GatewayDynamicConfig.native_classes_whitelist` Never Propagated from `ConfigManager` to Gateway — Stale Execution Mode Used for Stateful Validation — (`crates/apollo_config_manager/src/config_manager.rs`, `crates/apollo_gateway/src/gateway.rs`)

---

### Summary

`NodeDynamicConfig` stores `gateway_dynamic_config` (including `native_classes_whitelist`) and updates it via `ConfigManager::set_node_dynamic_config`. However, the `Gateway` component's `config: Arc<GatewayConfig>` is frozen at startup and never refreshed. There is no `GetGatewayDynamicConfig` variant in `ConfigManagerRequest`/`ConfigManagerResponse`, and the Gateway is never given a `config_manager_client`. Any runtime change to `gateway_dynamic_config.native_classes_whitelist` is silently ignored by the Gateway, which continues to use the stale startup value for stateful validation.

---

### Finding Description

**Two sources of truth, one never updated.**

`NodeDynamicConfig` carries `gateway_dynamic_config: Option<GatewayDynamicConfig>`:

```rust
// crates/apollo_node_config/src/node_config.rs
pub struct NodeDynamicConfig {
    pub gateway_dynamic_config: Option<GatewayDynamicConfig>,
    // ...
}
```

`NodeDynamicConfig::from(&SequencerNodeConfig)` extracts it from `gateway_config.dynamic_config`:

```rust
let gateway_dynamic_config = sequencer_node_config
    .gateway_config
    .as_ref()
    .map(|gateway_config| gateway_config.dynamic_config.clone());
```

`ConfigManager::set_node_dynamic_config` stores the updated value:

```rust
let mut config = self.latest_node_dynamic_config.write().await;
*config = node_dynamic_config;
```

`ConfigManagerRunner::update_config` calls this on every config-file change, so `gateway_dynamic_config` is live in `ConfigManager`.

**But the Gateway never reads it back.** `ConfigManagerRequest` and `ConfigManagerResponse` have no `GetGatewayDynamicConfig` variant:

```rust
// crates/apollo_config_manager_types/src/communication.rs
pub enum ConfigManagerResponse {
    GetConsensusDynamicConfig(...),
    GetClassManagerDynamicConfig(...),
    GetContextDynamicConfig(...),
    GetHttpServerDynamicConfig(...),
    GetMempoolDynamicConfig(...),
    GetBatcherDynamicConfig(...),
    GetStateSyncDynamicConfig(...),
    GetStakingManagerDynamicConfig(...),
    SetNodeDynamicConfig(...),
    // ← NO GetGatewayDynamicConfig
}
```

`ConfigManagerClient` has no `get_gateway_dynamic_config` method. The `handle_request` macro in `ConfigManager` lists every component except the Gateway:

```rust
handle_config_request!(
    self, request,
    (GetBatcherDynamicConfig, get_batcher_dynamic_config),
    (GetClassManagerDynamicConfig, get_class_manager_dynamic_config),
    // ... all others ...
    // ← Gateway absent
)
```

The Gateway is constructed with a frozen `Arc<GatewayConfig>` and no `config_manager_client`:

```rust
// crates/apollo_node/src/components.rs
Some(create_gateway(
    gateway_config.clone(),   // ← startup snapshot, never refreshed
    state_sync_client,
    mempool_client,
    class_manager_client,
    proof_manager_client,
    tokio::runtime::Handle::current(),
    // ← no config_manager_client passed
))
```

`GenericGateway` holds `config: Arc<GatewayConfig>` and reads `config.dynamic_config.native_classes_whitelist` during stateful validation. This value is the startup snapshot and is never updated.

**Contrast with `ClassManager`**, which correctly polls on every request:

```rust
// crates/apollo_class_manager/src/communication.rs
let dynamic_config = self.0.config_manager_client
    .get_class_manager_dynamic_config().await
    .expect("...");
self.0.update_dynamic_config(dynamic_config);
```

The Gateway has no equivalent pull.

---

### Impact Explanation

`GatewayDynamicConfig.native_classes_whitelist` controls which contract classes are executed via Cairo native compilation during the Gateway's stateful validation (fee estimation, `__validate__` simulation). `BatcherDynamicConfig.native_classes_whitelist` is a separate field that **is** properly propagated.

When an operator updates `native_classes_whitelist` (e.g., restricts it from `All` to `Limited([])` to disable native execution after discovering a correctness bug in a native-compiled class):

- `ConfigManager` stores the new value in `NodeDynamicConfig.gateway_dynamic_config`.
- The **Batcher** reads the updated value and switches to CASM for execution.
- The **Gateway** continues to use the stale `All` whitelist and still runs native execution during stateful validation.

Native and CASM execution can produce divergent results (gas consumption, revert/success, storage reads). The Gateway's admission decision is therefore made under a different execution mode than the Batcher's actual execution. Transactions that would revert under CASM may pass Gateway validation under native, and vice versa — causing the Gateway to accept transactions that the Batcher will revert, or reject transactions the Batcher would accept.

This matches: **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

---

### Likelihood Explanation

The `ConfigManagerRunner` watches the config file and calls `set_node_dynamic_config` automatically on every file change. Any operator who edits `native_classes_whitelist` in the config file (a documented, supported operation — see `deployments/sequencer/configs/overlays/hybrid/mainnet/common.yaml` which sets it to a specific class hash list) will trigger the divergence. No privileged on-chain action is required; a config file edit suffices.

---

### Recommendation

1. Add `GetGatewayDynamicConfig` to `ConfigManagerRequest`, `ConfigManagerResponse`, and `ConfigManagerClient` (mirroring the existing `GetBatcherDynamicConfig` pattern).

2. Add `get_gateway_dynamic_config` to `ConfigManager`:
   ```rust
   pub(crate) async fn get_gateway_dynamic_config(&self)
       -> ConfigManagerResult<GatewayDynamicConfig> {
       let config = self.latest_node_dynamic_config.read().await;
       Ok(config.gateway_dynamic_config.as_ref().unwrap().clone())
   }
   ```

3. Pass `config_manager_client` to the Gateway at construction and poll for `gateway_dynamic_config` on each `handle_request`, updating `self.config.dynamic_config` — exactly as `ClassManager::handle_request` does for `ClassManagerDynamicConfig`.

---

### Proof of Concept

1. Start the node with `native_classes_whitelist = "All"` in the config file.
2. The Gateway is constructed with `config.dynamic_config.native_classes_whitelist = All`.
3. Edit the config file to set `native_classes_whitelist = "[]"` (disable native execution).
4. `ConfigManagerRunner` detects the change and calls `set_node_dynamic_config` with the new `NodeDynamicConfig`.
5. `ConfigManager.latest_node_dynamic_config.gateway_dynamic_config.native_classes_whitelist` is now `Limited([])`.
6. The Batcher polls `get_batcher_dynamic_config` and switches to CASM.
7. The Gateway never polls; `config.dynamic_config.native_classes_whitelist` remains `All`.
8. Submit a transaction invoking a contract whose native-compiled class produces a different gas/revert result than its CASM equivalent.
9. The Gateway validates with native (stale `All`) → accepts.
10. The Batcher executes with CASM (updated `Limited([])`) → reverts.
11. The Gateway has admitted a transaction that the sequencer will revert, diverging admission from execution.

**Key code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_node_config/src/node_config.rs (L358-379)
```rust
#[derive(Debug, Deserialize, Serialize, Clone, PartialEq, Validate, Default)]
#[validate(schema(function = "validate_node_dynamic_config"))]
pub struct NodeDynamicConfig {
    #[validate(nested)]
    pub batcher_dynamic_config: Option<BatcherDynamicConfig>,
    #[validate(nested)]
    pub class_manager_dynamic_config: Option<ClassManagerDynamicConfig>,
    #[validate(nested)]
    pub consensus_dynamic_config: Option<ConsensusDynamicConfig>,
    #[validate(nested)]
    pub context_dynamic_config: Option<ContextDynamicConfig>,
    #[validate(nested)]
    pub gateway_dynamic_config: Option<GatewayDynamicConfig>,
    #[validate(nested)]
    pub http_server_dynamic_config: Option<HttpServerDynamicConfig>,
    #[validate(nested)]
    pub mempool_dynamic_config: Option<MempoolDynamicConfig>,
    #[validate(nested)]
    pub staking_manager_dynamic_config: Option<StakingManagerDynamicConfig>,
    #[validate(nested)]
    pub state_sync_dynamic_config: Option<StateSyncDynamicConfig>,
}
```

**File:** crates/apollo_node_config/src/node_config.rs (L425-428)
```rust
        let gateway_dynamic_config = sequencer_node_config
            .gateway_config
            .as_ref()
            .map(|gateway_config| gateway_config.dynamic_config.clone());
```

**File:** crates/apollo_config_manager/src/config_manager.rs (L132-150)
```rust
#[async_trait]
impl ComponentRequestHandler<ConfigManagerRequest, ConfigManagerResponse> for ConfigManager {
    async fn handle_request(&mut self, request: ConfigManagerRequest) -> ConfigManagerResponse {
        // Note: the `ConfigManagerRequest::SetNodeDynamicConfig` variant is handled inside the
        // macro.
        handle_config_request!(
            self,
            request,
            (GetBatcherDynamicConfig, get_batcher_dynamic_config),
            (GetClassManagerDynamicConfig, get_class_manager_dynamic_config),
            (GetConsensusDynamicConfig, get_consensus_dynamic_config),
            (GetContextDynamicConfig, get_context_dynamic_config),
            (GetHttpServerDynamicConfig, get_http_server_dynamic_config),
            (GetMempoolDynamicConfig, get_mempool_dynamic_config),
            (GetStakingManagerDynamicConfig, get_staking_manager_dynamic_config),
            (GetStateSyncDynamicConfig, get_state_sync_dynamic_config),
        )
    }
}
```

**File:** crates/apollo_config_manager_types/src/communication.rs (L95-106)
```rust
#[derive(Clone, Serialize, Deserialize, AsRefStr)]
pub enum ConfigManagerResponse {
    GetConsensusDynamicConfig(ConfigManagerResult<ConsensusDynamicConfig>),
    GetClassManagerDynamicConfig(ConfigManagerResult<ClassManagerDynamicConfig>),
    GetContextDynamicConfig(ConfigManagerResult<ContextDynamicConfig>),
    GetHttpServerDynamicConfig(ConfigManagerResult<HttpServerDynamicConfig>),
    GetMempoolDynamicConfig(ConfigManagerResult<MempoolDynamicConfig>),
    GetBatcherDynamicConfig(ConfigManagerResult<BatcherDynamicConfig>),
    GetStateSyncDynamicConfig(ConfigManagerResult<StateSyncDynamicConfig>),
    GetStakingManagerDynamicConfig(ConfigManagerResult<StakingManagerDynamicConfig>),
    SetNodeDynamicConfig(ConfigManagerResult<()>),
}
```

**File:** crates/apollo_node/src/components.rs (L241-267)
```rust
    let gateway = match config.components.gateway.execution_mode {
        ReactiveComponentExecutionMode::LocalExecutionWithRemoteDisabled
        | ReactiveComponentExecutionMode::LocalExecutionWithRemoteEnabled => {
            let gateway_config =
                config.gateway_config.as_ref().expect("Gateway config should be set");
            let mempool_client =
                clients.get_mempool_shared_client().expect("Mempool client should be available");
            let state_sync_client = clients
                .get_state_sync_shared_client()
                .expect("State Sync client should be available");
            let class_manager_client = clients
                .get_class_manager_shared_client()
                .expect("Class Manager client should be available");
            let proof_manager_client = clients
                .get_proof_manager_shared_client()
                .expect("Proof Manager client should be available");
            Some(create_gateway(
                gateway_config.clone(),
                state_sync_client,
                mempool_client,
                class_manager_client,
                proof_manager_client,
                tokio::runtime::Handle::current(),
            ))
        }
        ReactiveComponentExecutionMode::Disabled | ReactiveComponentExecutionMode::Remote => None,
    };
```

**File:** crates/apollo_class_manager/src/communication.rs (L20-29)
```rust
impl ComponentRequestHandler<ClassManagerRequest, ClassManagerResponse> for ClassManager {
    async fn handle_request(&mut self, request: ClassManagerRequest) -> ClassManagerResponse {
        let dynamic_config: apollo_class_manager_config::config::ClassManagerDynamicConfig = self
            .0
            .config_manager_client
            .get_class_manager_dynamic_config()
            .await
            .expect("Should be able to get class manager dynamic config");
        self.0.update_dynamic_config(dynamic_config);

```

**File:** crates/apollo_gateway_config/src/config.rs (L149-157)
```rust
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, Validate)]
pub struct GatewayDynamicConfig {
    pub native_classes_whitelist: NativeClassesWhitelist,
}

impl Default for GatewayDynamicConfig {
    fn default() -> Self {
        Self { native_classes_whitelist: NativeClassesWhitelist::All }
    }
```
