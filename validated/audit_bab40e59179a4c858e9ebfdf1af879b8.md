Based on my review, this is a valid finding: the `resources()` JSON path binds the type-layout resolution to the wrong ledger version.

### Title
Historical Resource Bytes Decoded Using Latest-Version Type Layout in `Account::resources` JSON Response - (File: api/src/accounts.rs)

### Summary
In `Account::resources` (JSON branch), raw resource bytes are correctly fetched at the caller-requested historical `self.ledger_version` via `get_resources_by_pagination`, but they are subsequently decoded into `MoveResource` JSON using a converter built from `latest_state_view_poem`, i.e., a state view bound to the *latest* ledger version rather than `self.ledger_version`.

### Finding Description
`get_account_resources` builds an `Account` with the requested `ledger_version` and calls `account.resources(&accept_type)` [1](#0-0) . Inside `resources()`, the raw BCS resource bytes are correctly fetched at `self.ledger_version` via `get_resources_by_pagination` [2](#0-1) .

However, for the JSON `AcceptType`, the decoding `state_view` used to build the `converter` (and thus resolve struct/type layouts via ABI/module metadata) is obtained from `self.context.latest_state_view_poem(&self.latest_ledger_info)` — a state view pinned to the latest ledger version, not `self.ledger_version`: [3](#0-2) 

This is inconsistent with the sibling method `find_resource` in the same file, which correctly derives its state view from the requested version via `self.context.state_view(Some(self.ledger_version))`: [4](#0-3) 

Because `try_into_resources`/the converter resolves Move struct layouts (field names, types, enum variants) using module bytecode/ABI available in the supplied state view, using the *latest* state view means the byte-to-JSON field mapping reflects the current on-chain module layout — not the layout that was live at the historically requested `ledger_version`. If a module's struct layout changed between the historical version and the present (e.g., added/removed/reordered fields, or changed field types), the raw historical bytes (correct, from the old layout) get reinterpreted with the new layout's field schema, producing corrupted or nonsensical JSON field values for a query that is supposed to represent state as of the older version.

### Impact Explanation
This is an authenticated-response/state-view binding defect: an API response explicitly scoped to `ledger_version=N` returns `MoveResource` JSON fields decoded under the module layout at the latest version, misrepresenting what state actually looked like at version `N`. This falls under the required impact "Authenticated API or state-view output bound to the wrong version, object, or proof context." Any consumer relying on `GET /accounts/:address/resources?ledger_version=N` to audit or reconstruct historical state (e.g., indexers, wallets, forensic/compliance tools, or governance snapshotting) can be given a JSON object whose field values do not correspond to the actual state committed at version `N`. Note the BCS branch is unaffected because it returns raw bytes untouched (`AcceptType::Bcs` path at lines 498–507), so the corruption is limited to the JSON convenience representation, not the underlying committed ledger data itself.

### Likelihood Explanation
Triggering this requires only unprivileged, standard API usage: any caller can request `/accounts/:address/resources` with an old `ledger_version` for an account holding a resource type whose struct layout was later upgraded (a routine, common occurrence for framework and application modules across upgrades). No special privileges, timing races, or malicious behavior are needed — just a module upgrade occurring between the historical version and "now," which is a normal and frequent event on mainnet. This makes it fairly high likelihood in practice for any long-lived module that evolves its resource schema.

### Recommendation
In `Account::resources` JSON branch, construct the converter's state view bound to `self.ledger_version` (mirroring `find_resource`'s pattern using `self.context.state_view(Some(self.ledger_version))`) instead of `self.context.latest_state_view_poem(&self.latest_ledger_info)`, ensuring ABI/type-layout resolution used by `try_into_resources` matches the same version the raw resource bytes were fetched from.

### Proof of Concept
1. Deploy a module `M` with `struct S { a: u64 }` and publish a resource of type `S` under an account at version `V1`.
2. Upgrade the module to `struct S { a: u64, b: bool }` (or reorder/retype fields) at version `V2 > V1`, without touching the account's stored resource bytes (bytes remain those written under the old layout).
3. Call `GET /accounts/:address/resources?ledger_version=V1` with `Accept: application/json`.
4. Observe: the raw bytes fetched are the historical `V1` bytes (correct), but the converter (built from `latest_state_view_poem`, i.e., pinned to the current/latest version) decodes them using the new `S` layout from `V2`, producing an incorrect/garbled `b` field value (or shifted/misaligned field values) in the JSON response — diverging from the true historical resource content, while the BCS response for the same query correctly returns the original historical bytes.

### Citations

**File:** api/src/accounts.rs (L117-126)
```rust
        api_spawn_blocking(move || {
            let account = Account::new(
                context,
                address.0,
                ledger_version.0,
                start.0.map(StateKey::from),
                limit.0,
            )?;
            account.resources(&accept_type)
        })
```

**File:** api/src/accounts.rs (L448-463)
```rust
    pub fn resources(self, accept_type: &AcceptType) -> BasicResultWith404<Vec<MoveResource>> {
        let max_account_resources_page_size = self.context.max_account_resources_page_size();
        let (resources, next_state_key) = self
            .context
            .get_resources_by_pagination(
                self.address.into(),
                self.start.as_ref(),
                self.ledger_version,
                // Just use the max as the default
                determine_limit(
                    self.limit,
                    max_account_resources_page_size,
                    max_account_resources_page_size,
                    &self.latest_ledger_info,
                )? as u64,
            )
```

**File:** api/src/accounts.rs (L473-490)
```rust
        match accept_type {
            AcceptType::Json => {
                // Resolve the BCS encoded versions into `MoveResource`s
                let state_view = self
                    .context
                    .latest_state_view_poem(&self.latest_ledger_info)?;
                let converter = state_view
                    .as_converter(self.context.db.clone(), self.context.indexer_reader.clone());
                let converted_resources = converter
                    .try_into_resources(resources.iter().map(|(k, v)| (k.clone(), v.as_slice())))
                    .context("Failed to build move resource response from data in DB")
                    .map_err(|err| {
                        BasicErrorWith404::internal_with_code(
                            err,
                            AptosErrorCode::InternalError,
                            &self.latest_ledger_info,
                        )
                    })?;
```

**File:** api/src/accounts.rs (L658-663)
```rust
        let (ledger_info, requested_ledger_version, state_view) =
            self.context.state_view(Some(self.ledger_version))?;

        let bytes = state_view
            .as_converter(self.context.db.clone(), self.context.indexer_reader.clone())
            .find_resource(&state_view, self.address, resource_type)
```
