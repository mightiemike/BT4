[1](#0-0) [2](#0-1)

### Citations

**File:** bin/citrea/src/rollup/bitcoin.rs (L68-104)
```rust
    #[instrument(level = "trace", skip_all, err)]
    fn create_rpc_methods(
        &self,
        node_type: NodeType,
        storage: ProverStorage,
        ledger_db: &LedgerDB,
        da_service: &Arc<Self::DaService>,
        backup_manager: &Arc<BackupManager>,
        rpc_config: RpcConfig,
    ) -> Result<jsonrpsee::RpcModule<()>, anyhow::Error> {
        let mut rpc_methods = RpcModule::new(());

        if !matches!(node_type, NodeType::LightClientProver) {
            let methods = <CitreaRuntime<DefaultContext, Self::DaSpec>>::rpc_methods(
                storage,
                ledger_db.clone(),
            );

            rpc_methods.merge(methods)?;
        }

        let ledger_db_methods = sov_ledger_rpc::server::create_rpc_module::<LedgerDB>(
            ledger_db.clone(),
            rpc_config.into(),
        );
        rpc_methods.merge(ledger_db_methods)?;

        let backup_methods = create_backup_rpc_module(ledger_db.clone(), backup_manager.clone());
        rpc_methods.merge(backup_methods)?;

        if matches!(node_type, NodeType::BatchProver) || matches!(node_type, NodeType::Sequencer) {
            let da_methods = create_da_rpc_module(da_service.clone());
            rpc_methods.merge(da_methods)?;
        }

        Ok(rpc_methods)
    }
```

**File:** crates/evm/src/query.rs (L1-62)
```rust
use std::ops::{Range, RangeInclusive};

use alloy_consensus::constants::{EMPTY_RECEIPTS, EMPTY_TRANSACTIONS};
use alloy_consensus::{
    Block as AlloyConsensusBlock, BlockBody, Header as AlloyConsensusHeader,
    Transaction as AlloyTransaction, TxReceipt, EMPTY_OMMER_ROOT_HASH,
};
use alloy_eips::eip2718::Encodable2718;
use alloy_eips::eip2930::AccessListWithGasUsed;
use alloy_eips::eip7685::EMPTY_REQUESTS_HASH;
use alloy_eips::{BlockId, BlockNumHash, BlockNumberOrTag};
use alloy_network::AnyTransactionReceipt;
use alloy_primitives::TxKind::{Call, Create};
use alloy_primitives::{Address, Bloom, Bytes, Uint, B256, U256, U64};
use alloy_rpc_types::state::StateOverride;
use alloy_rpc_types::{
    AnyReceiptEnvelope, BlockOverrides, BloomFilter, Filter, FilterBlockOption, FilteredParams,
    Header as AlloyHeader, Log, ReceiptWithBloom, Transaction, TransactionInfo, TransactionReceipt,
};
use alloy_rpc_types_eth::transaction::TransactionRequest;
use alloy_rpc_types_eth::Block as AlloyRpcBlock;
use alloy_rpc_types_trace::geth::{
    GethDebugTracingCallOptions, GethDebugTracingOptions, GethTrace, TraceResult,
};
use alloy_serde::{OtherFields, WithOtherFields};
use citrea_primitives::basefee::calculate_next_block_base_fee;
use citrea_primitives::forks::fork_from_block_number;
use jsonrpsee::core::RpcResult;
use reth_primitives::{Recovered, SealedHeader, TransactionSigned};
use reth_provider::ProviderError;
use reth_rpc::eth::filter::EthFilterError;
use reth_rpc::eth::EthTxBuilder;
use reth_rpc_eth_api::TransactionCompat;
use reth_rpc_eth_types::error::{
    ensure_success, EthApiError, EthResult, RevertError, RpcInvalidTransactionError,
};
use reth_rpc_eth_types::logs_utils::log_matches_filter;
use revm::context::result::{EVMError, ExecutionResult, HaltReason, InvalidTransaction};
use revm::context::{BlockEnv, Cfg, CfgEnv, TransactTo};
use revm::context_interface::block::BlobExcessGasAndPrice;
use revm::primitives::hardfork::SpecId;
use revm::{Database, DatabaseCommit};
use revm_inspectors::access_list::AccessListInspector;
use revm_inspectors::tracing::{TracingInspector, TracingInspectorConfig};
use serde::{Deserialize, Serialize};
use sov_db::ledger_db::NodeLedgerOps;
use sov_db::schema::types::L2HeightStatus;
use sov_modules_api::fork::Fork;
use sov_modules_api::macros::rpc_gen;
use sov_modules_api::prelude::*;
use sov_modules_api::{SpecId as CitreaSpecId, WorkingSet};

use crate::call::get_cfg_env;
use crate::conversions::{create_tx_env, sealed_block_to_block_env};
use crate::evm::call::{create_txn_env, prepare_call_env};
use crate::evm::db::EvmDb;
use crate::evm::primitive_types::{
    CitreaReceiptWithBloom, SealedBlock, TransactionSignedAndRecovered,
};
use crate::handler::{diff_size_send_eth_eoa, TxInfo};
use crate::rpc_helpers::*;
use crate::{citrea_spec_id_to_evm_spec_id, Evm, EvmChainConfig};
```
