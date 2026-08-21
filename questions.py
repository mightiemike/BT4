import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 22
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'tronprotocol/java-tron'
# todo: the name of the repository
REPO_NAME = 'java-tron'

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    # =================================================================================
    # Public HTTP entrypoints: request parsing, param decoding, error handling
    # =================================================================================
    "framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java",
    "framework/src/main/java/org/tron/core/services/http/Util.java",
    "framework/src/main/java/org/tron/core/services/http/PostParams.java",
    "framework/src/main/java/org/tron/core/services/http/JsonFormat.java",
    "framework/src/main/java/org/tron/core/services/http/HttpSelfFormatFieldName.java",
    "framework/src/main/java/org/tron/core/services/http/BroadcastServlet.java",
    "framework/src/main/java/org/tron/core/services/http/BroadcastHexServlet.java",
    "framework/src/main/java/org/tron/core/services/http/TriggerSmartContractServlet.java",
    "framework/src/main/java/org/tron/core/services/http/TriggerConstantContractServlet.java",
    "framework/src/main/java/org/tron/core/services/http/EstimateEnergyServlet.java",
    "framework/src/main/java/org/tron/core/services/http/DeployContractServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateCommonTransactionServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionSignWeightServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionApprovedListServlet.java",

    # =================================================================================
    # Unbounded / paginated read endpoints reachable by any anonymous RPC client
    # =================================================================================
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedExchangeListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedProposalListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedNowWitnessListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAssetIssueListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAssetIssueListByNameServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetMarketOrderListByPairServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetMarketOrderByAccountServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBlockByLimitNextServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBlockByLatestNumServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionInfoByBlockNumServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionListFromPendingServlet.java",

    # =================================================================================
    # JSON-RPC surface: eth_* dispatch, filters, log queries, argument coercion
    # =================================================================================
    "framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilterWrapper.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogBlockQuery.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/BuildArguments.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/CallArguments.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/BlockResult.java",

    # =================================================================================
    # gRPC surface and the query/limit gates every API path depends on
    # =================================================================================
    "framework/src/main/java/org/tron/core/services/RpcApiService.java",
    "framework/src/main/java/org/tron/core/Wallet.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/RpcApiAccessInterceptor.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterContainer.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/IPQPSRateLimiterAdapter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/QpsRateLimiterAdapter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/GlobalPreemptibleAdapter.java",
    "framework/src/main/java/org/tron/core/services/filter/HttpApiAccessFilter.java",
    "framework/src/main/java/org/tron/core/services/filter/LiteFnQueryHttpFilter.java",
    "framework/src/main/java/org/tron/core/services/filter/LiteFnQueryGrpcInterceptor.java",
    "framework/src/main/java/org/tron/core/services/filter/CachedBodyRequestWrapper.java",
    "framework/src/main/java/org/tron/common/application/HttpService.java",
    "framework/src/main/java/org/tron/common/application/RpcService.java",

    # =================================================================================
    # Transaction admission: signature/permission verification, dedup, mempool
    # =================================================================================
    "chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java",
    "actuator/src/main/java/org/tron/core/utils/TransactionUtil.java",
    "actuator/src/main/java/org/tron/core/utils/TransactionRegister.java",
    "chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java",
    "framework/src/main/java/org/tron/core/db/Manager.java",
    "framework/src/main/java/org/tron/core/db/PendingManager.java",
    "chainbase/src/main/java/org/tron/core/db/TransactionCache.java",
    "chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java",
    "chainbase/src/main/java/org/tron/core/db/TransactionTrace.java",
    "chainbase/src/main/java/org/tron/core/db/TransactionContext.java",
    "chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java",

    # =================================================================================
    # Actuators: authorization and balance/state invariants of every user operation
    # =================================================================================
    "actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ActuatorCreator.java",
    "actuator/src/main/java/org/tron/core/actuator/TransferActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java",
    "actuator/src/main/java/org/tron/core/actuator/CancelAllUnfreezeV2Actuator.java",
    "actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnfreezeAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ClearABIContractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/VMActuator.java",

    # =================================================================================
    # TVM: interpreter, gas/energy accounting, memory, storage, precompiles
    # =================================================================================
    "actuator/src/main/java/org/tron/core/vm/VM.java",
    "actuator/src/main/java/org/tron/core/vm/JumpTable.java",
    "actuator/src/main/java/org/tron/core/vm/OperationActions.java",
    "actuator/src/main/java/org/tron/core/vm/OperationRegistry.java",
    "actuator/src/main/java/org/tron/core/vm/EnergyCost.java",
    "actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java",
    "actuator/src/main/java/org/tron/core/vm/MessageCall.java",
    "actuator/src/main/java/org/tron/core/vm/VMUtils.java",
    "actuator/src/main/java/org/tron/core/vm/program/Program.java",
    "actuator/src/main/java/org/tron/core/vm/program/Memory.java",
    "actuator/src/main/java/org/tron/core/vm/program/Stack.java",
    "actuator/src/main/java/org/tron/core/vm/program/Storage.java",
    "actuator/src/main/java/org/tron/core/vm/program/ContractState.java",
    "actuator/src/main/java/org/tron/core/vm/program/ProgramPrecompile.java",
    "actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeFactory.java",
    "actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeImpl.java",
    "actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java",
    "actuator/src/main/java/org/tron/core/vm/utils/MUtil.java",
    "actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java",
    "actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java",
    "framework/src/main/java/org/tron/common/runtime/RuntimeImpl.java",
    "chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java",

    # =================================================================================
    # TVM native staking contracts callable from any attacker-deployed contract
    # =================================================================================
    "actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawExpireUnfreezeProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java",

    # =================================================================================
    # Resource model: bandwidth/energy metering, delegation and reward accounting
    # =================================================================================
    "chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java",
    "chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java",
    "chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java",
    "chainbase/src/main/java/org/tron/core/store/DelegationStore.java",
    "chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java",
    "chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java",
    "chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java",
    "chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java",
    "chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java",
    "chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java",
    "chainbase/src/main/java/org/tron/common/utils/Commons.java",

    # =================================================================================
    # Iteration and lookup layers behind account/asset/market queries
    # =================================================================================
    "chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java",
    "chainbase/src/main/java/org/tron/core/db/TronDatabase.java",
    "chainbase/src/main/java/org/tron/core/db/common/iterator/DBIterator.java",
    "chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java",
    "chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java",
    "chainbase/src/main/java/org/tron/core/store/AccountStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java",
    "chainbase/src/main/java/org/tron/core/store/AssetIssueV2Store.java",
    "chainbase/src/main/java/org/tron/core/store/MarketOrderStore.java",
    "chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java",
    "chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java",
    "chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java",
    "chainbase/src/main/java/org/tron/core/store/StorageRowStore.java",
    "chainbase/src/main/java/org/tron/core/store/ContractStore.java",
    "chainbase/src/main/java/org/tron/core/store/CodeStore.java",
    "chainbase/src/main/java/org/tron/core/ChainBaseManager.java",

    # =================================================================================
    # Cryptographic primitives used for signature, hash and precompile verification
    # =================================================================================
    "crypto/src/main/java/org/tron/common/crypto/ECKey.java",
    "crypto/src/main/java/org/tron/common/crypto/Rsv.java",
    "crypto/src/main/java/org/tron/common/crypto/SignUtils.java",
    "crypto/src/main/java/org/tron/common/crypto/Hash.java",
    "crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java",
    "crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java",
    "crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java",
    "crypto/src/main/java/org/tron/keystore/Wallet.java",
    "crypto/src/main/java/org/tron/keystore/WalletUtils.java",
    "crypto/src/main/java/org/tron/keystore/Credentials.java",

    # =================================================================================
    # Encoding, address and math helpers on every attacker-controlled input path
    # =================================================================================
    "common/src/main/java/org/tron/common/utils/ByteArray.java",
    "common/src/main/java/org/tron/common/utils/ByteUtil.java",
    "common/src/main/java/org/tron/common/utils/DecodeUtil.java",
    "common/src/main/java/org/tron/common/utils/Base58.java",
    "common/src/main/java/org/tron/common/utils/Bech32.java",
    "common/src/main/java/org/tron/common/utils/Sha256Hash.java",
    "common/src/main/java/org/tron/common/utils/MerkleRoot.java",
    "common/src/main/java/org/tron/common/utils/StringUtil.java",
    "common/src/main/java/org/tron/common/utils/BIUtil.java",
    "common/src/main/java/org/tron/common/math/Maths.java",
    "common/src/main/java/org/tron/common/math/StrictMathWrapper.java",
    "common/src/main/java/org/tron/common/utils/CompactEncoder.java",
    "common/src/main/java/org/tron/common/parameter/CommonParameter.java",
    "framework/src/main/java/org/tron/core/capsule/utils/RLP.java",
    "chainbase/src/main/java/org/tron/common/utils/WalletUtil.java",

    # =================================================================================
    # Zero-knowledge / shielded transaction verification reachable from transactions
    # =================================================================================
    "chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java",
    "chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java",
    "chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleVoucherContainer.java",
    "chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java",
    "chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java",
    "chainbase/src/main/java/org/tron/common/zksnark/ZksnarkUtils.java",
    "framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java",
    "framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java",
    "framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java",
    "framework/src/main/java/org/tron/core/zen/address/KeyIo.java",

    # =================================================================================
    # Reward, vote and parameter state transitions driven by user transactions
    # =================================================================================
    "consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java",
    "consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java",
    "consensus/src/main/java/org/tron/consensus/dpos/StatisticManager.java",
    "consensus/src/main/java/org/tron/consensus/ConsensusDelegate.java",
    "framework/src/main/java/org/tron/core/consensus/ProposalService.java",
    "actuator/src/main/java/org/tron/core/utils/ProposalUtil.java",
    "chainbase/src/main/java/org/tron/common/utils/ForkController.java",
    "actuator/src/main/java/org/tron/core/vm/config/ConfigLoader.java",

    # =================================================================================
    # Event and log emission driven by attacker-controlled contract data
    # =================================================================================
    "framework/src/main/java/org/tron/common/logsfilter/ContractEventParser.java",
    "framework/src/main/java/org/tron/common/logsfilter/ContractEventParserAbi.java",
    "framework/src/main/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsule.java",
    "framework/src/main/java/org/tron/common/logsfilter/capsule/TransactionLogTriggerCapsule.java",
    "chainbase/src/main/java/org/tron/common/bloom/Bloom.java",
    "framework/src/main/java/org/tron/core/services/NodeInfoService.java",
    "framework/src/main/java/org/tron/core/metrics/MetricsApiService.java",
]


target_scopes = [
    "Fatal. An unprivileged attacker who can only broadcast a transaction or deploy/trigger a smart contract achieves remote code execution or full node takeover on a default full node, through native-library parameter handling, deserialization, reflection, file or process access reachable from transaction, TVM or RPC input.",
    "Fatal. An unprivileged attacker recovers a node operator's or a user's private key, spending key, or keystore secret, through key material leaked into RPC responses, logs, error messages, event triggers, or through nonce/randomness reuse or a biased signing path in ECKey, SM2, or the keystore.",
    "Critical. An unprivileged attacker moves, spends, freezes, or destroys assets of an account they do not control, by defeating signature recovery, multi-signature permission weight accounting, owner-address checks, or contract-caller identity in an actuator, TVM native contract, or precompiled contract.",
    "Critical. An unprivileged attacker mints value out of nothing or corrupts global accounting — TRX/TRC10 balance, frozen or delegated resource, exchange or market order, withdrawn reward or brokerage — through integer overflow, sign confusion, rounding, unit mismatch, or a missing conservation check in an actuator, resource processor, reward calculation, or exchange/market math.",
    "Critical. An unprivileged attacker makes a full node compute state that diverges from the rest of the network, or accept a transaction the network rejects, through non-deterministic math, fork-gate or version-condition mismatch, cache/store inconsistency, or an energy/bandwidth metering difference between execution and validation paths.",
    "Advanced. A remote attacker with no privileges causes sustained denial of service on a node's RPC-API by sending crafted HTTP, JSON-RPC, or gRPC requests that bypass the rate limiter or trigger unbounded iteration, unbounded allocation, quadratic work, deadlock, or an uncaught fatal error inside a query handler.",
    "Advanced. An unprivileged attacker halts, stalls, or crashes a node through the TRON protocol implementation itself, by broadcasting cheaply-constructed transactions or contract calls whose validation, TVM execution, resource metering, or persistence cost is disproportionate to the fee and energy actually charged.",
    "Intermediate. An unprivileged attacker performs an account operation without the account owner's authorization, or blocks a legitimate owner's operation permanently, by abusing permission update, account-id, asset, vote, proposal-approve, delegation, or unfreeze logic to lock, squat, or overwrite state the owner controls.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one java-tron target.

    ```
    target_file format:
    "'File Name: actuator/src/main/java/org/tron/core/actuator/TransferActuator.java -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact java-tron target:

    {target_file}

    Project focus:
    java-tron is the TRON full node. Focus on transaction validation and signature/permission checks, actuator state transitions, TVM execution and energy metering, precompiled contracts, bandwidth/energy/delegation and reward accounting, exchange and market math, store iteration behind queries, and the HTTP/gRPC/JSON-RPC handlers plus their rate limiters.

    Rules:
    * Treat `File Name:` as the exact file/class.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Java symbols (class, method, field, constant) when possible.
    * Attacker is unprivileged only: an anonymous RPC/HTTP/JSON-RPC client, an ordinary funded TRON account broadcasting signed transactions, or anyone deploying and calling a smart contract.
    * Attacker is NOT a witness/SR, node operator, committee member, or peer, holds no leaked keys, and cannot rely on malicious peers, malicious nodes, P2P message handling, or majority stake.
    * Ignore test files, mocks, benchmarks, generated protobuf stubs, docs, build/CI config, and dependency-only issues.
    * Ignore self-harm (attacker damaging only their own account) and economic-design critique.
    * Generate 12 to 16 high-signal questions.
    * At least 70% must target unauthorized account operations, asset or accounting corruption, signature/permission bypass, key or secret disclosure, node RCE, consensus divergence, or DoS via RPC-API or protocol implementation.
    * Every question must be testable by a JUnit test, a crafted transaction/contract call, a raw RPC request, or a differential/fuzz test over encoded inputs.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authorization is exact: every balance, resource, asset, or contract mutation requires a signature that recovers to an address holding sufficient permission weight for that operation.
    * Value is conserved: sum of balances, frozen/delegated resources, asset supply, exchange reserves, and rewards never increases except through defined issuance, and never underflows.
    * Metering is faithful: consumed bandwidth and energy are charged before or exactly in step with the work performed, and no cheap input yields unbounded CPU, memory, disk, or iteration.
    * Execution is deterministic: identical input yields identical state, receipts, and energy across nodes, JDKs, and fork-gate states.
    * Secrets stay internal: private keys, spending keys, and keystore material never reach an RPC response, log line, event trigger, or error message.

    Each question must include:
    1. target class/method;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: Class.method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: JUnit/transaction/RPC/fuzz INPUTS and assert AUTHORIZATION_ENFORCED, VALUE_CONSERVATION, FAITHFUL_METERING, DETERMINISM, or SECRET_CONFINEMENT.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused java-tron exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: an anonymous RPC/HTTP/JSON-RPC client, an ordinary funded account broadcasting signed transactions, or a smart contract deployer/caller. No witness/SR, committee, node-operator, or peer role. No leaked keys. No malicious-peer, malicious-node, P2P, or 51% assumptions.
- Reject anything requiring privileged addresses, physical or local-network access, MITM, social engineering, or non-default node configuration.
- Reject anything that depends only on test/mock/benchmark/docs/build files, dependency bugs alone, or best-practice cleanup without exploitable impact.
- Focus on real compromise paths: node RCE, private-key disclosure, unauthorized account operations, asset/accounting corruption, consensus divergence, DoS via RPC-API, and DoS via the TRON protocol implementation.

## Validate
- Trace the exact reachable path from attacker input (HTTP/JSON-RPC/gRPC request, broadcast transaction, or contract call) into the affected method.
- Check whether existing checks already stop it: `TransactionCapsule.validateSignature`, permission weight and owner-address checks, `DecodeUtil.addressValid`, actuator `validate()`, fork gates in `ForkController`, energy/bandwidth accounting, rate limiters, and query size limits.
- Account for real chain economics: fees, energy and bandwidth cost, and transaction size limits the attacker must pay.
- Accept only concrete impact: stolen or frozen assets, created or destroyed value, unauthorized state change, key/secret leak, node crash or stall, or divergent state.
- Require exact file/method support and a reproducible JUnit or request-level PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and matching TRON bounty impact class]

### Likelihood Explanation
[Preconditions, cost to attacker, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[JUnit test, crafted transaction/contract call, or raw RPC sequence with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for java-tron security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject privileged-actor (witness/SR, committee, node operator, peer), leaked-key, physical/local-network, MITM, social-engineering, dependency-only, docs/style, and test/mock/config-only issues.
- Reject malicious-peer, malicious-node, P2P-message, Sybil, 51%, and pure-DDoS claims.
- Reject self-harm, economic-design critique, scanner output, and theoretical claims with no demonstrated impact.
- A valid report must be triggerable by an anonymous RPC client, an ordinary funded account, or a smart contract caller against a default full node.
- The final impact must map to an in-scope class: RCE/node takeover, private-key disclosure, DoS via RPC-API, DoS via the TRON protocol implementation, unauthorized account operations, or asset/consensus integrity loss.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, class, method, and line/code references.
2. Clear root cause and broken security assumption.
3. Reachable exploit path: preconditions -> attacker transaction/contract call/RPC request -> trigger -> bad result.
4. Existing validation, permission checks, fork gates, metering, and rate limits reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood and attacker cost.
6. Reproducible proof path: JUnit PoC, crafted transaction, or exact RPC sequence against a default node.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an anonymous RPC client or ordinary account trigger this with no privileged role and no leaked key?
- Does the code actually behave as claimed on the current release version?
- Is the impact caused by java-tron code, not by node configuration or a dependency alone?
- Is the theft, accounting break, key leak, divergence, or crash concrete and not hypothetical?
- Is the attacker's fee/energy cost low enough for the claimed impact to matter?
- Would a TRON HackerOne triager accept the proof?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and TRON bounty category]

## Likelihood Explanation
[Attacker capability, required conditions, cost, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible transaction/RPC sequence or JUnit test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for java-tron.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged analogs in transaction/signature/permission validation, actuator state transitions, TVM execution and energy metering, precompiled contracts, resource and reward accounting, exchange/market math, store iteration, or HTTP/gRPC/JSON-RPC handlers and rate limiters.
- Reject privileged-actor, leaked-key, malicious-peer/node, P2P, dependency-only, test-only, and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable java-tron path from an anonymous RPC request, a broadcast transaction, or a contract call.
- Prove root cause with exact file/class/method support.
- Accept only concrete node RCE, key disclosure, unauthorized account operation, asset or accounting corruption, consensus divergence, or DoS via RPC-API or protocol implementation.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
