### Title
Overly permissive hex-number parsing in `ResourceBounds`/`Tip` deserialization allows spec-violating transaction fields (`+`-prefixed digits) that diverge from the canonical `NUM_AS_HEX` format - (File: crates/starknet_api/src/transaction/fields.rs)

### Summary
The Tornado advisory describes how `int()`-based parsing of `Content-Length`/chunk-length silently accepts `+`, `-`, and `_` characters that the HTTP RFC forbids, letting different implementations disagree on the same byte stream. The sequencer has an analogous class of bug: transaction fee fields (`tip`, `max_amount`, `max_price_per_unit`) are parsed from JSON hex strings using Rust's `from_str_radix`, which silently accepts a leading `+` sign even though the Starknet RPC spec's `NUM_AS_HEX` pattern (`^0x[a-fA-F0-9]+$`) forbids anything but hex digits after `0x`.

### Finding Description
`ResourceBounds` and `Tip` are deserialized via custom functions that trim the `0x` prefix and hand the remainder straight to `u64`/`u128::from_str_radix(.., 16)`: [1](#0-0) 

Rust's integer `from_str_radix` accepts an optional leading `+` sign for both signed and unsigned integer types (only `-` is rejected for unsigned types). Consequently a value such as `"0x+64"` passes `trim_start_matches("0x")` (producing `"+64"`) and then successfully parses via `from_str_radix` to `100`, even though the JSON-RPC schema that governs the wire format explicitly restricts these values to `^0x[a-fA-F0-9]+$`: [2](#0-1) 

This same `from_str_radix`-after-`trim_start_matches("0x")` pattern is repeated for `max_amount`/`max_price_per_unit` and for the legacy `EntryPointOffset` hex parser, indicating it is a systemic idiom rather than an isolated bug: [3](#0-2) 

None of the gateway's stateless validation steps re-checks the raw string format of these hex fields before or after this parsing; `validate_resource_bounds` only inspects the already-decoded numeric values (min gas price, zero bounds, max l2 gas amount): [4](#0-3) 

So a `tip`/`resource_bounds` value like `"0x+5"`, `"0x+1a"`, or similar (with an embedded `+`) is silently accepted by the sequencer's transaction deserialization pipeline used both at ingestion (`deserialize_transaction_json_to_starknet_api_tx`) and at OS/re-execution replay: [5](#0-4) 

### Impact Explanation
Any component in the Starknet ecosystem that validates numeric hex fields strictly against the published `NUM_AS_HEX` pattern (wallets/SDKs, third-party full-node implementations, indexers, or a stricter/updated version of this same sequencer) will reject a transaction containing such a "+"-decorated field as malformed, while this sequencer's gateway/serde layer accepts it, hashes it, executes it, and can include it in a committed block. This is a direct instance of the RFC-vs-implementation mismatch that defines the referenced CWE-444 class: the same bytes are interpreted as valid by one component and invalid/differently by another, which is the root cause category for honest-node divergence and inability to reach network-wide agreement on transaction validity for spec-conformant validators. Because the divergent field feeds directly into fee accounting (`tip`, `max_amount`, `max_price_per_unit`) and the transaction hash inputs, any downstream verifier that independently re-parses the RPC-level string (rather than trusting the already-decoded `Felt`) can disagree with the sequencer about whether the transaction — and therefore the block containing it — is even well-formed.

### Likelihood Explanation
Any unprivileged transaction sender can trigger this by submitting an otherwise valid V3 transaction with a hex field crafted as `"0x+<digits>"` for `tip`, or any `resource_bounds.*.max_amount`/`max_price_per_unit`. No special privileges, timing, or race conditions are required — it is a pure input-crafting issue reachable through the standard gateway `add_tx` path.

### Recommendation
Replace the permissive `from_str_radix` calls in `hex_to_gas_amount`, `hex_to_gas_price`, `hex_to_tip` (crates/starknet_api/src/transaction/fields.rs) and `hex_string_try_into_usize` (crates/starknet_api/src/deprecated_contract_class.rs) with strict validation that rejects any character outside `[0-9a-fA-F]` after the `0x` prefix (e.g., validate with a regex or manually check `chars().all(|c| c.is_ascii_hexdigit())` before calling `from_str_radix`), matching the `NUM_AS_HEX` schema exactly.

### Proof of Concept
1. Submit an INVOKE V3 transaction whose JSON body sets `"tip": "0x+5"` (or `resource_bounds.l2_gas.max_price_per_unit: "0x+64"`) to the gateway's `add_tx` endpoint.
2. Observe that `hex_to_tip`/`hex_to_gas_price` successfully deserialize this into `Tip(5)`/`GasPrice(100)` because `"+5"`/`"+64"` is accepted by `u64/u128::from_str_radix(.., 16)`.
3. Compare against the RPC spec's `NUM_AS_HEX` JSON-schema pattern `^0x[a-fA-F0-9]+$`, which does not allow a `+` character, showing a strict conformance-checking component would reject the identical payload that this sequencer accepts.

### Citations

**File:** crates/starknet_api/src/transaction/fields.rs (L275-308)
```rust
fn hex_to_gas_amount<'de, D>(deserializer: D) -> Result<GasAmount, D::Error>
where
    D: Deserializer<'de>,
{
    let s: String = Deserialize::deserialize(deserializer)?;
    Ok(GasAmount(
        u64::from_str_radix(s.trim_start_matches("0x"), 16).map_err(serde::de::Error::custom)?,
    ))
}

fn gas_price_to_hex<S>(value: &GasPrice, serializer: S) -> Result<S::Ok, S::Error>
where
    S: Serializer,
{
    serializer.serialize_str(&format!("0x{:x}", value.0))
}

fn hex_to_gas_price<'de, D>(deserializer: D) -> Result<GasPrice, D::Error>
where
    D: Deserializer<'de>,
{
    let s: String = Deserialize::deserialize(deserializer)?;
    Ok(GasPrice(
        u128::from_str_radix(s.trim_start_matches("0x"), 16).map_err(serde::de::Error::custom)?,
    ))
}

pub fn hex_to_tip<'de, D>(deserializer: D) -> Result<Tip, D::Error>
where
    D: Deserializer<'de>,
{
    let s: String = Deserialize::deserialize(deserializer)?;
    Ok(Tip(u64::from_str_radix(s.trim_start_matches("0x"), 16).map_err(serde::de::Error::custom)?))
}
```

**File:** crates/apollo_rpc/resources/V0_8/starknet_write_api.json (L211-215)
```json
            "NUM_AS_HEX": {
                "title": "An integer number in hex format (0x...)",
                "type": "string",
                "pattern": "^0x[a-fA-F0-9]+$"
            },
```

**File:** crates/starknet_api/src/deprecated_contract_class.rs (L225-227)
```rust
fn hex_string_try_into_usize(hex_string: &str) -> Result<usize, std::num::ParseIntError> {
    usize::from_str_radix(hex_string.trim_start_matches("0x"), 16)
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
    }
```

**File:** crates/starknet_api/src/serde_utils.rs (L181-199)
```rust
pub fn deserialize_transaction_json_to_starknet_api_tx(
    mut raw_transaction: Value,
) -> serde_json::Result<Transaction> {
    let tx_type: String = serde_json::from_value(raw_transaction["type"].clone())?;
    let tx_version: String = serde_json::from_value(raw_transaction["version"].clone())?;

    // rpc_v8 fix (remove redundantly added L1DataGas)
    let raw_resourcebounds = &raw_transaction["resource_bounds"];
    if !raw_resourcebounds.is_null()
        && !raw_resourcebounds["l1_data_gas"].is_null()
        && raw_resourcebounds["l1_data_gas"]["max_amount"] == "0x0"
        && !raw_resourcebounds["l2_gas"].is_null()
        && raw_resourcebounds["l2_gas"]["max_amount"] == "0x0"
    {
        raw_transaction["resource_bounds"]
            .as_object_mut()
            .expect("should be map of resource bounds")
            .remove("l1_data_gas");
    }
```
