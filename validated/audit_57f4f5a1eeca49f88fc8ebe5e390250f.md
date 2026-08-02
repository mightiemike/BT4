No vulnerability found for this question.

**Analysis:**

The `on_new_epoch` no-op pattern in `jwk_consensus_config.move` is standard across all on-chain config modules (e.g., `execution_config`, `epoch_timeout_config`, `gas_schedule`) — the resource is only mutated when a pending config was queued via `config_buffer::upsert`/`set_for_next_epoch`, otherwise it's intentionally left untouched: [1](#0-0) 

This is by design and matches the documented behavior ("Only used in reconfigurations to apply the pending `JWKConsensusConfig`, if there is any"), not a bug: leaving a resource unchanged when there's nothing to apply doesn't corrupt state, storage, or proof material.

The premise that a client could be "misled" about freshness doesn't hold against the actual authenticated read path. Aptos's state-view/storage layer never claims a resource was written "as of the current epoch" — it returns the resource bound to its true last-write version. `DbStateView::get` and `DbReader::get_state_value_with_version_by_version` explicitly return a `(Version, StateValue)` tuple where `Version` is the actual last-modified version of that state slot, not the queried ledger version or current epoch: [2](#0-1) [3](#0-2) 

The public REST API (`/accounts/{address}/resource/{resource_type}`) returns the resource contents plus an `X-APTOS-LEDGER-VERSION` header for the *chain's* current version — it never asserts the resource itself was last modified at that version or at the current epoch boundary: [4](#0-3) 

So there is no code path in the repo that binds `JWKConsensusConfig` (or any config resource) to an epoch/version it wasn't actually written at. Any "freshness == current epoch" assumption would have to originate entirely in a hypothetical external client that ignores the version metadata the storage layer already correctly provides — that is a client-side design flaw, not a repo root cause, and it doesn't corrupt committed state, proof material, or authenticated API version binding. This falls outside the State-Integrity Gate (no wrong write set, root, proof, or version is produced or accepted) and matches an excluded category ("changes presentation without corrupting authenticated state").

### Citations

**File:** aptos-move/framework/aptos-framework/sources/configs/jwk_consensus_config.move (L64-75)
```text
    /// Only used in reconfigurations to apply the pending `JWKConsensusConfig`, if there is any.
    public(friend) fun on_new_epoch(framework: &signer) acquires JWKConsensusConfig {
        system_addresses::assert_aptos_framework(framework);
        if (config_buffer::does_exist<JWKConsensusConfig>()) {
            let new_config = config_buffer::extract_v2<JWKConsensusConfig>();
            if (exists<JWKConsensusConfig>(@aptos_framework)) {
                *borrow_global_mut<JWKConsensusConfig>(@aptos_framework) = new_config;
            } else {
                move_to(framework, new_config);
            };
        }
    }
```

**File:** storage/storage-interface/src/state_store/state_view/db_state_view.rs (L26-46)
```rust
impl DbStateView {
    fn get(&self, key: &StateKey) -> StateViewResult<Option<(Version, StateValue)>> {
        if let Some(version) = self.version {
            if let Some(root_hash) = self.maybe_verify_against_state_root_hash {
                // TODO(aldenhu): sample-verify proof inside DB
                // DB doesn't support returning proofs for buffered state, so only optionally
                // verify proof.
                // TODO: support returning state proof for buffered state.
                if let Ok((value, proof)) =
                    self.db.get_state_value_with_proof_by_version(key, version)
                {
                    proof.verify(root_hash, *key.crypto_hash_ref(), value.as_ref())?;
                }
            }
            Ok(self
                .db
                .get_state_value_with_version_by_version(key, version)?)
        } else {
            Ok(None)
        }
    }
```

**File:** storage/aptosdb/src/state_store/tests/speculative_state_workflow.rs (L523-530)
```rust
impl DbReader for StateByVersion {
    fn get_state_value_with_version_by_version(
        &self,
        state_key: &StateKey,
        version: Version,
    ) -> DbResult<Option<(Version, StateValue)>> {
        Ok(self.get_state(Some(version)).state.get(state_key).cloned())
    }
```

**File:** api/src/state.rs (L38-84)
```rust
    /// Get account resource
    ///
    /// Retrieves an individual resource from a given account and at a specific ledger version. If the
    /// ledger version is not specified in the request, the latest ledger version is used.
    ///
    /// The Aptos nodes prune account state history, via a configurable time window.
    /// If the requested ledger version has been pruned, the server responds with a 410.
    #[oai(
        path = "/accounts/:address/resource/:resource_type",
        method = "get",
        operation_id = "get_account_resource",
        tag = "ApiTags::Accounts"
    )]
    async fn get_account_resource(
        &self,
        accept_type: AcceptType,
        /// Address of account with or without a `0x` prefix
        address: Path<Address>,
        /// Name of struct to retrieve e.g. `0x1::account::Account`
        resource_type: Path<MoveStructTag>,
        /// Ledger version to get state of account
        ///
        /// If not provided, it will be the latest version
        ledger_version: Query<Option<U64>>,
    ) -> BasicResultWith404<MoveResource> {
        fail_point_poem("endpoint_get_account_resource")?;
        self.context
            .check_api_output_enabled("Get account resource", &accept_type)?;
        resource_type
            .0
            .verify(0)
            .context("'resource_type' invalid")
            .map_err(|err| {
                BasicErrorWith404::bad_request_with_code_no_info(err, AptosErrorCode::InvalidInput)
            })?;

        let api = self.clone();
        api_spawn_blocking(move || {
            api.resource(
                &accept_type,
                address.0,
                resource_type.0,
                ledger_version.0.map(|inner| inner.0),
            )
        })
        .await
    }
```
