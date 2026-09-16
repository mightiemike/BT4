Found a concrete analog. `DeprecatedGatewayDeclareTransactionV3`, `DeprecatedGatewayDeployAccountTransactionV3`, and `DeprecatedGatewayInvokeTransactionV3` in `crates/apollo_http_server/src/deprecated_gateway_transaction.rs` are missing `#[serde(deny_unknown_fields)]`, unlike essentially every other user-facing transaction struct in this codebase (`RpcTransaction`, `IntermediateDeclareTransaction`, `InvokeV0Transaction`, etc. at [1](#0-0) , [2](#0-1) ). This is the direct analog of "policy JSON accepts unknown security fields" — the outer enum wrapper has `deny_unknown_fields` at the tag level, but the inner variant payload structs do not.

### Title
Deprecated gateway transaction structs silently accept unknown/misspelled fields, unlike all other transaction deserializers - ([File: crates/apollo_http_server/src/deprecated_gateway_transaction.rs])

### Summary
`DeprecatedGatewayDeclareTransactionV3`, `DeprecatedGatewayDeployAccountTransactionV3`, and `DeprecatedGatewayInvokeTransactionV3` — the structs backing the legacy Pythonic-Gateway `add_transaction` HTTP endpoint — lack `#[serde(deny_unknown_fields)]`, while every other transaction-facing struct in the sequencer enforces it.

### Finding Description
The sequencer's transaction deserialization consistently applies `#[serde(deny_unknown_fields)]` on every user-submitted-transaction struct that reaches the gateway or RPC layer: `RpcTransaction` [3](#0-2) , `InternalRpcTransactionWithoutTxHash` [4](#0-3) , all feeder-gateway/writer transaction variants (`InvokeV0Transaction`, `InvokeV1Transaction`, `DeployAccountV1Transaction`, `IntermediateDeclareTransaction`, `IntermediateDeployAccountTransaction`, `DeployTransaction`) [5](#0-4) , and there is a dedicated regression test asserting unknown fields cause deserialization failure [6](#0-5) .

By contrast, in `crates/apollo_http_server/src/deprecated_gateway_transaction.rs`, the outer wrapper enum `DeprecatedGatewayTransactionV3` does carry `#[serde(deny_unknown_fields)]` [7](#0-6) , but the concrete payload structs for each transaction type — `DeprecatedGatewayDeclareTransactionV3` [8](#0-7) , `DeprecatedGatewayDeployAccountTransactionV3` [9](#0-8) , and `DeprecatedGatewayInvokeTransactionV3` [10](#0-9)  — carry no `deny_unknown_fields` attribute at all. This mirrors the reported bug class exactly: a security-relevant JSON schema that is supposed to be strict silently accepts unknown/misspelled fields because the enforcement (`deny_unknown_fields`) was applied only at one nesting level and not propagated to the inner struct that actually parses attacker-controlled fields (the same class of defect as the "serde `deny_unknown_fields` does not work well with `flatten`" issue explicitly called out elsewhere in this codebase) [11](#0-10) .

### Impact Explanation
An unprivileged sender submitting a transaction through the deprecated/legacy gateway HTTP endpoint can include extra, unrecognized fields (e.g. a misspelled variant of `account_deployment_data`, `paymaster_data`, `nonce_data_availability_mode`, or any other security-relevant field) in the JSON body without triggering a deserialization error. Because these three structs are converted directly into the canonical `RpcDeclareTransactionV3`/`RpcDeployAccountTransactionV3`/`RpcInvokeTransactionV3` types via explicit field-by-field `From`/convert impls [12](#0-11) , any field name a client intends to set is either taken from the correctly-spelled key or silently dropped if misspelled/unknown — with no error surfaced to the client. This weakens the defense-in-depth the rest of the codebase relies on (explicit test coverage confirming unknown fields must be rejected) and creates a schema-integrity gap specific to the legacy ingestion path that every other transaction parser in the repo explicitly guards against.

### Likelihood Explanation
High likelihood of being hit accidentally (client typos silently ignored instead of erroring) and straightforward to trigger deliberately: any external submitter of legacy-format transactions via the Pythonic gateway HTTP endpoint can add arbitrary extra/misnamed keys to a `DECLARE`/`DEPLOY_ACCOUNT`/`INVOKE_FUNCTION` V3 JSON payload and have it accepted rather than rejected, since only the outer enum discriminant is checked strictly.

### Recommendation
Add `#[serde(deny_unknown_fields)]` to `DeprecatedGatewayDeclareTransactionV3`, `DeprecatedGatewayDeployAccountTransactionV3`, and `DeprecatedGatewayInvokeTransactionV3` (and audit `DeprecatedGatewaySierraContractClass` similarly), matching the convention used by every other transaction-facing struct in the codebase, and add a regression test analogous to `load_transaction_unknown_field_fails` for the deprecated gateway path.

### Proof of Concept
1. Submit a legacy-format `DECLARE`/`DEPLOY_ACCOUNT`/`INVOKE_FUNCTION` V3 transaction to the Pythonic gateway HTTP endpoint (routed to `DeprecatedGatewayTransactionV3`).
2. Add an extra unknown key inside the inner transaction object, e.g. `{"type": "INVOKE_FUNCTION", "version": "0x3", "sender_address": ..., "unknown_field": "x", ...}`.
3. Observe that deserialization into `DeprecatedGatewayInvokeTransactionV3` succeeds (unlike the equivalent test `load_transaction_unknown_field_fails` for other transaction types, which asserts failure) [6](#0-5) , confirming the schema-enforcement gap.

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L61-69)
```rust
#[serde(tag = "type")]
#[serde(deny_unknown_fields)]
pub enum RpcTransaction {
    #[serde(rename = "DECLARE")]
    Declare(RpcDeclareTransaction),
    #[serde(rename = "DEPLOY_ACCOUNT")]
    DeployAccount(RpcDeployAccountTransaction),
    #[serde(rename = "INVOKE")]
    Invoke(RpcInvokeTransaction),
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L104-113)
```rust
#[serde(tag = "type")]
#[serde(deny_unknown_fields)]
pub enum InternalRpcTransactionWithoutTxHash {
    #[serde(rename = "DECLARE")]
    Declare(InternalRpcDeclareTransactionV3),
    #[serde(rename = "DEPLOY_ACCOUNT")]
    DeployAccount(InternalRpcDeployAccountTransaction),
    #[serde(rename = "INVOKE")]
    Invoke(InternalRpcInvokeTransactionV3),
}
```

**File:** crates/apollo_starknet_client/src/writer/objects/transaction.rs (L137-147)
```rust
#[derive(Debug, Default, Deserialize, Serialize, Clone, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct InvokeV0Transaction {
    pub calldata: Calldata,
    pub contract_address: ContractAddress,
    pub max_fee: Fee,
    pub signature: TransactionSignature,
    pub version: TransactionVersion,
    pub r#type: InvokeType,
    pub entry_point_selector: EntryPointSelector,
}
```

**File:** crates/apollo_starknet_client/src/reader/objects/transaction_test.rs (L66-83)
```rust
#[test]
fn load_transaction_unknown_field_fails() {
    for file_name in [
        "reader/deploy_v0.json",
        "reader/invoke_v0.json",
        "reader/declare_v0.json",
        "reader/deploy_account_v3.json",
    ] {
        let mut json_value: serde_json::Value =
            serde_json::from_str(&read_resource_file(file_name)).unwrap();
        json_value
            .as_object_mut()
            .unwrap()
            .insert("unknown_field".to_string(), serde_json::Value::Null);
        let json_str = serde_json::to_string(&json_value).unwrap();
        assert!(serde_json::from_str::<Transaction>(&json_str).is_err(), "filename: {file_name}");
    }
}
```

**File:** crates/apollo_http_server/src/deprecated_gateway_transaction.rs (L41-51)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
#[serde(tag = "type")]
#[serde(deny_unknown_fields)]
pub enum DeprecatedGatewayTransactionV3 {
    #[serde(rename = "DECLARE")]
    Declare(DeprecatedGatewayDeclareTransaction),
    #[serde(rename = "DEPLOY_ACCOUNT")]
    DeployAccount(DeprecatedGatewayDeployAccountTransaction),
    #[serde(rename = "INVOKE_FUNCTION")]
    Invoke(DeprecatedGatewayInvokeTransaction),
}
```

**File:** crates/apollo_http_server/src/deprecated_gateway_transaction.rs (L103-119)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub struct DeprecatedGatewayInvokeTransactionV3 {
    pub sender_address: ContractAddress,
    pub calldata: Calldata,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub resource_bounds: DeprecatedGatewayAllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
    #[serde(default, skip_serializing_if = "Proof::is_empty")]
    pub proof: Proof,
}
```

**File:** crates/apollo_http_server/src/deprecated_gateway_transaction.rs (L167-179)
```rust
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub struct DeprecatedGatewayDeployAccountTransactionV3 {
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub class_hash: ClassHash,
    pub contract_address_salt: ContractAddressSalt,
    pub constructor_calldata: Calldata,
    pub resource_bounds: DeprecatedGatewayAllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
}
```

**File:** crates/apollo_http_server/src/deprecated_gateway_transaction.rs (L223-236)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub struct DeprecatedGatewayDeclareTransactionV3 {
    pub sender_address: ContractAddress,
    pub compiled_class_hash: CompiledClassHash,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub contract_class: DeprecatedGatewaySierraContractClass,
    pub resource_bounds: DeprecatedGatewayAllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
}
```

**File:** crates/apollo_http_server/src/deprecated_gateway_transaction.rs (L238-257)
```rust
impl DeprecatedGatewayDeclareTransactionV3 {
    pub fn convert_to_rpc_declare_tx(
        self,
        max_size: usize,
    ) -> Result<RpcDeclareTransactionV3, CompressionError> {
        Ok(RpcDeclareTransactionV3 {
            sender_address: self.sender_address,
            compiled_class_hash: self.compiled_class_hash,
            signature: self.signature,
            nonce: self.nonce,
            contract_class: self.contract_class.convert_to_sierra_contract_class(max_size)?,
            resource_bounds: self.resource_bounds.into(),
            tip: self.tip,
            paymaster_data: self.paymaster_data,
            account_deployment_data: self.account_deployment_data,
            nonce_data_availability_mode: self.nonce_data_availability_mode,
            fee_data_availability_mode: self.fee_data_availability_mode,
        })
    }
}
```

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L1013-1017)
```rust
#[derive(Debug, Clone, Eq, Hash, PartialEq, Deserialize, Serialize, PartialOrd, Ord)]
#[serde(tag = "type")]
// Applying deny_unknown_fields on the inner type instead of on PendingTransactionReceipt because
// of a bug that makes deny_unknown_fields not work well with flatten:
// https://github.com/serde-rs/serde/issues/1358
```
