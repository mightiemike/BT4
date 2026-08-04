import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = "tronprotocol/java-tron"
# todo: the name of the repository
REPO_NAME = "java-tron"

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
    # Transaction, permission, and VM execution
    # =================================================================================
    "actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ActuatorConstant.java",
    "actuator/src/main/java/org/tron/core/actuator/ActuatorCreator.java",
    "actuator/src/main/java/org/tron/core/actuator/ActuatorFactory.java",
    "actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/CancelAllUnfreezeV2Actuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ClearABIContractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java",
    "actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ProposalCreateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ProposalDeleteActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/TransferActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnfreezeAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateBrokerageActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/VMActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java",
    "actuator/src/main/java/org/tron/core/actuator/WitnessUpdateActuator.java",
    "actuator/src/main/java/org/tron/core/utils/ProposalUtil.java",
    "actuator/src/main/java/org/tron/core/utils/TransactionRegister.java",
    "actuator/src/main/java/org/tron/core/utils/TransactionUtil.java",
    "actuator/src/main/java/org/tron/core/utils/ZenChainParams.java",
    "actuator/src/main/java/org/tron/core/vm/ChainParameterEnum.java",
    "actuator/src/main/java/org/tron/core/vm/EnergyCost.java",
    "actuator/src/main/java/org/tron/core/vm/JumpTable.java",
    "actuator/src/main/java/org/tron/core/vm/LogInfoTriggerParser.java",
    "actuator/src/main/java/org/tron/core/vm/MessageCall.java",
    "actuator/src/main/java/org/tron/core/vm/Op.java",
    "actuator/src/main/java/org/tron/core/vm/Operation.java",
    "actuator/src/main/java/org/tron/core/vm/OperationActions.java",
    "actuator/src/main/java/org/tron/core/vm/OperationRegistry.java",
    "actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java",
    "actuator/src/main/java/org/tron/core/vm/VM.java",
    "actuator/src/main/java/org/tron/core/vm/VMConstant.java",
    "actuator/src/main/java/org/tron/core/vm/VMUtils.java",
    "actuator/src/main/java/org/tron/core/vm/config/ConfigLoader.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawExpireUnfreezeProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/param/CancelAllUnfreezeV2Param.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/param/DelegateResourceParam.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/param/FreezeBalanceParam.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/param/FreezeBalanceV2Param.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/param/UnDelegateResourceParam.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/param/UnfreezeBalanceParam.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/param/UnfreezeBalanceV2Param.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/param/VoteWitnessParam.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/param/WithdrawExpireUnfreezeParam.java",
    "actuator/src/main/java/org/tron/core/vm/nativecontract/param/WithdrawRewardParam.java",
    "actuator/src/main/java/org/tron/core/vm/program/ContractState.java",
    "actuator/src/main/java/org/tron/core/vm/program/Memory.java",
    "actuator/src/main/java/org/tron/core/vm/program/Program.java",
    "actuator/src/main/java/org/tron/core/vm/program/ProgramPrecompile.java",
    "actuator/src/main/java/org/tron/core/vm/program/Stack.java",
    "actuator/src/main/java/org/tron/core/vm/program/Storage.java",
    "actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvoke.java",
    "actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeFactory.java",
    "actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeImpl.java",
    "actuator/src/main/java/org/tron/core/vm/program/listener/CompositeProgramListener.java",
    "actuator/src/main/java/org/tron/core/vm/program/listener/ProgramListener.java",
    "actuator/src/main/java/org/tron/core/vm/program/listener/ProgramListenerAdaptor.java",
    "actuator/src/main/java/org/tron/core/vm/program/listener/ProgramListenerAware.java",
    "actuator/src/main/java/org/tron/core/vm/program/listener/ProgramStorageChangeListener.java",
    "actuator/src/main/java/org/tron/core/vm/repository/Key.java",
    "actuator/src/main/java/org/tron/core/vm/repository/Repository.java",
    "actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java",
    "actuator/src/main/java/org/tron/core/vm/repository/Type.java",
    "actuator/src/main/java/org/tron/core/vm/repository/Value.java",
    "actuator/src/main/java/org/tron/core/vm/trace/Op.java",
    "actuator/src/main/java/org/tron/core/vm/trace/OpActions.java",
    "actuator/src/main/java/org/tron/core/vm/trace/ProgramTrace.java",
    "actuator/src/main/java/org/tron/core/vm/trace/ProgramTraceListener.java",
    "actuator/src/main/java/org/tron/core/vm/trace/Serializers.java",
    "actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java",
    "actuator/src/main/java/org/tron/core/vm/utils/MUtil.java",
    "actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java",

    # =================================================================================
    # State, accounting, and storage
    # =================================================================================
    "chainbase/src/main/java/org/tron/common/runtime/CallCreate.java",
    "chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java",
    "chainbase/src/main/java/org/tron/common/runtime/ProgramResult.java",
    "chainbase/src/main/java/org/tron/common/runtime/Runtime.java",
    "chainbase/src/main/java/org/tron/common/utils/Commons.java",
    "chainbase/src/main/java/org/tron/common/utils/WalletUtil.java",
    "chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java",
    "chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleVoucherContainer.java",
    "chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java",
    "chainbase/src/main/java/org/tron/common/zksnark/JLibsodium.java",
    "chainbase/src/main/java/org/tron/common/zksnark/JLibsodiumParam.java",
    "chainbase/src/main/java/org/tron/common/zksnark/LibrustzcashParam.java",
    "chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java",
    "chainbase/src/main/java/org/tron/common/zksnark/MerklePath.java",
    "chainbase/src/main/java/org/tron/common/zksnark/ZksnarkUtils.java",
    "chainbase/src/main/java/org/tron/core/ChainBaseManager.java",
    "chainbase/src/main/java/org/tron/core/actuator/Actuator.java",
    "chainbase/src/main/java/org/tron/core/actuator/Actuator2.java",
    "chainbase/src/main/java/org/tron/core/actuator/TransactionFactory.java",
    "chainbase/src/main/java/org/tron/core/capsule/AbiCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/AccountTraceCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/BlockBalanceTraceCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/BlockCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/BytesCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/CodeCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/ContractCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java",
    "chainbase/src/main/java/org/tron/core/capsule/IncrementalMerkleTreeCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/IncrementalMerkleVoucherCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/MarketAccountOrderCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/MarketOrderIdListCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/MarketPriceCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/PbftSignCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/PedersenHashCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/ProtoCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java",
    "chainbase/src/main/java/org/tron/core/capsule/StorageRowCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/TransactionInfoCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/TransactionResultCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/TransactionRetCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/WitnessCapsule.java",
    "chainbase/src/main/java/org/tron/core/capsule/utils/AssetUtil.java",
    "chainbase/src/main/java/org/tron/core/capsule/utils/BlockUtil.java",
    "chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java",
    "chainbase/src/main/java/org/tron/core/capsule/utils/MerkleTree.java",
    "chainbase/src/main/java/org/tron/core/capsule/utils/TransactionUtil.java",
    "chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java",
    "chainbase/src/main/java/org/tron/core/db/BlockIndexStore.java",
    "chainbase/src/main/java/org/tron/core/db/BlockStore.java",
    "chainbase/src/main/java/org/tron/core/db/CommonDataBase.java",
    "chainbase/src/main/java/org/tron/core/db/CommonStore.java",
    "chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java",
    "chainbase/src/main/java/org/tron/core/db/KhaosDatabase.java",
    "chainbase/src/main/java/org/tron/core/db/PbftSignDataStore.java",
    "chainbase/src/main/java/org/tron/core/db/RecentBlockStore.java",
    "chainbase/src/main/java/org/tron/core/db/RecentTransactionItem.java",
    "chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java",
    "chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java",
    "chainbase/src/main/java/org/tron/core/db/RevokingDatabase.java",
    "chainbase/src/main/java/org/tron/core/db/TransactionCache.java",
    "chainbase/src/main/java/org/tron/core/db/TransactionContext.java",
    "chainbase/src/main/java/org/tron/core/db/TransactionStore.java",
    "chainbase/src/main/java/org/tron/core/db/TransactionTrace.java",
    "chainbase/src/main/java/org/tron/core/db/TronDatabase.java",
    "chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java",
    "chainbase/src/main/java/org/tron/core/db/accountstate/AccountStateCallBackUtils.java",
    "chainbase/src/main/java/org/tron/core/db/accountstate/AccountStateEntity.java",
    "chainbase/src/main/java/org/tron/core/db2/core/AbstractSnapshot.java",
    "chainbase/src/main/java/org/tron/core/db2/core/Chainbase.java",
    "chainbase/src/main/java/org/tron/core/db2/core/ITronChainBase.java",
    "chainbase/src/main/java/org/tron/core/db2/core/Snapshot.java",
    "chainbase/src/main/java/org/tron/core/db2/core/SnapshotImpl.java",
    "chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java",
    "chainbase/src/main/java/org/tron/core/db2/core/SnapshotRoot.java",
    "chainbase/src/main/java/org/tron/core/service/MortgageService.java",
    "chainbase/src/main/java/org/tron/core/service/RewardViCalService.java",
    "chainbase/src/main/java/org/tron/core/store/AbiStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountAssetStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountIndexStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountStore.java",
    "chainbase/src/main/java/org/tron/core/store/AccountTraceStore.java",
    "chainbase/src/main/java/org/tron/core/store/AssetIssueStore.java",
    "chainbase/src/main/java/org/tron/core/store/AssetIssueV2Store.java",
    "chainbase/src/main/java/org/tron/core/store/BalanceTraceStore.java",
    "chainbase/src/main/java/org/tron/core/store/CheckPointV2Store.java",
    "chainbase/src/main/java/org/tron/core/store/CheckTmpStore.java",
    "chainbase/src/main/java/org/tron/core/store/CodeStore.java",
    "chainbase/src/main/java/org/tron/core/store/ContractStateStore.java",
    "chainbase/src/main/java/org/tron/core/store/ContractStore.java",
    "chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java",
    "chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java",
    "chainbase/src/main/java/org/tron/core/store/DelegationStore.java",
    "chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java",
    "chainbase/src/main/java/org/tron/core/store/ExchangeStore.java",
    "chainbase/src/main/java/org/tron/core/store/ExchangeV2Store.java",
    "chainbase/src/main/java/org/tron/core/store/IncrementalMerkleTreeStore.java",
    "chainbase/src/main/java/org/tron/core/store/MarketAccountStore.java",
    "chainbase/src/main/java/org/tron/core/store/MarketOrderStore.java",
    "chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java",
    "chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java",
    "chainbase/src/main/java/org/tron/core/store/NullifierStore.java",
    "chainbase/src/main/java/org/tron/core/store/ProposalStore.java",
    "chainbase/src/main/java/org/tron/core/store/RewardViStore.java",
    "chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java",
    "chainbase/src/main/java/org/tron/core/store/StorageRowStore.java",
    "chainbase/src/main/java/org/tron/core/store/StoreFactory.java",
    "chainbase/src/main/java/org/tron/core/store/TransactionHistoryStore.java",
    "chainbase/src/main/java/org/tron/core/store/TransactionRetStore.java",
    "chainbase/src/main/java/org/tron/core/store/TreeBlockIndexStore.java",
    "chainbase/src/main/java/org/tron/core/store/VotesStore.java",
    "chainbase/src/main/java/org/tron/core/store/WitnessScheduleStore.java",
    "chainbase/src/main/java/org/tron/core/store/WitnessStore.java",
    "chainbase/src/main/java/org/tron/core/store/ZKProofStore.java",

    # =================================================================================
    # Shared runtime and crypto primitives
    # =================================================================================
    "common/src/main/java/org/tron/common/runtime/vm/DataWord.java",
    "common/src/main/java/org/tron/common/runtime/vm/LogInfo.java",
    "common/src/main/java/org/tron/common/utils/Base58.java",
    "common/src/main/java/org/tron/common/utils/Bech32.java",
    "common/src/main/java/org/tron/common/utils/ByteArray.java",
    "common/src/main/java/org/tron/common/utils/ByteArrayMap.java",
    "common/src/main/java/org/tron/common/utils/ByteArraySet.java",
    "common/src/main/java/org/tron/common/utils/ByteUtil.java",
    "common/src/main/java/org/tron/common/utils/CollectionUtils.java",
    "common/src/main/java/org/tron/common/utils/CompactEncoder.java",
    "common/src/main/java/org/tron/common/utils/DecodeUtil.java",
    "common/src/main/java/org/tron/common/utils/FastByteComparisons.java",
    "common/src/main/java/org/tron/common/utils/MerkleRoot.java",
    "common/src/main/java/org/tron/common/utils/Pair.java",
    "common/src/main/java/org/tron/common/utils/Sha256Hash.java",
    "common/src/main/java/org/tron/common/utils/StringUtil.java",
    "common/src/main/java/org/tron/common/utils/Time.java",
    "common/src/main/java/org/tron/common/utils/TypeConversion.java",
    "common/src/main/java/org/tron/common/utils/Utils.java",
    "common/src/main/java/org/tron/core/vm/config/VMConfig.java",
    "crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java",
    "crypto/src/main/java/org/tron/common/crypto/ECKey.java",
    "crypto/src/main/java/org/tron/common/crypto/Hash.java",
    "crypto/src/main/java/org/tron/common/crypto/Rsv.java",
    "crypto/src/main/java/org/tron/common/crypto/SignInterface.java",
    "crypto/src/main/java/org/tron/common/crypto/SignUtils.java",
    "crypto/src/main/java/org/tron/common/crypto/SignatureInterface.java",
    "crypto/src/main/java/org/tron/common/crypto/cryptohash/Digest.java",
    "crypto/src/main/java/org/tron/common/crypto/cryptohash/DigestEngine.java",
    "crypto/src/main/java/org/tron/common/crypto/cryptohash/Keccak256.java",
    "crypto/src/main/java/org/tron/common/crypto/cryptohash/Keccak512.java",
    "crypto/src/main/java/org/tron/common/crypto/cryptohash/KeccakCore.java",
    "crypto/src/main/java/org/tron/common/crypto/jce/ECAlgorithmParameters.java",
    "crypto/src/main/java/org/tron/common/crypto/jce/ECKeyAgreement.java",
    "crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java",
    "crypto/src/main/java/org/tron/common/crypto/jce/ECKeyPairGenerator.java",
    "crypto/src/main/java/org/tron/common/crypto/jce/ECSignatureFactory.java",
    "crypto/src/main/java/org/tron/common/crypto/jce/TronCastleProvider.java",
    "crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java",
    "crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128Fp2.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G1.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/Field.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/Fp12.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/Fp2.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/Fp6.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java",
    "crypto/src/main/java/org/tron/common/crypto/zksnark/Params.java",

    # =================================================================================
    # Wallet, API, and shielded entrypoints
    # =================================================================================
    "framework/src/main/java/org/tron/common/runtime/RuntimeImpl.java",
    "framework/src/main/java/org/tron/common/zksnark/ZksnarkClient.java",
    "framework/src/main/java/org/tron/core/Wallet.java",
    "framework/src/main/java/org/tron/core/capsule/ReceiveDescriptionCapsule.java",
    "framework/src/main/java/org/tron/core/capsule/SpendDescriptionCapsule.java",
    "framework/src/main/java/org/tron/core/capsule/TxInputCapsule.java",
    "framework/src/main/java/org/tron/core/capsule/TxOutputCapsule.java",
    "framework/src/main/java/org/tron/core/capsule/utils/DecodeResult.java",
    "framework/src/main/java/org/tron/core/capsule/utils/FastByteComparisons.java",
    "framework/src/main/java/org/tron/core/capsule/utils/RLP.java",
    "framework/src/main/java/org/tron/core/capsule/utils/RLPElement.java",
    "framework/src/main/java/org/tron/core/capsule/utils/RLPItem.java",
    "framework/src/main/java/org/tron/core/capsule/utils/RLPList.java",
    "framework/src/main/java/org/tron/core/capsule/utils/TxInputUtil.java",
    "framework/src/main/java/org/tron/core/capsule/utils/TxOutputUtil.java",
    "framework/src/main/java/org/tron/core/db/Manager.java",
    "framework/src/main/java/org/tron/core/db/PendingManager.java",
    "framework/src/main/java/org/tron/core/db/accountstate/AccountStateEntity.java",
    "framework/src/main/java/org/tron/core/db/accountstate/TrieService.java",
    "framework/src/main/java/org/tron/core/db/accountstate/callback/AccountStateCallBack.java",
    "framework/src/main/java/org/tron/core/db/accountstate/storetrie/AccountStateStoreTrie.java",
    "framework/src/main/java/org/tron/core/services/RpcApiService.java",
    "framework/src/main/java/org/tron/core/services/WalletOnCursor.java",
    "framework/src/main/java/org/tron/core/services/filter/HttpApiAccessFilter.java",
    "framework/src/main/java/org/tron/core/services/filter/HttpInterceptor.java",
    "framework/src/main/java/org/tron/core/services/filter/LiteFnQueryGrpcInterceptor.java",
    "framework/src/main/java/org/tron/core/services/filter/LiteFnQueryHttpFilter.java",
    "framework/src/main/java/org/tron/core/services/http/AccountPermissionUpdateServlet.java",
    "framework/src/main/java/org/tron/core/services/http/BroadcastHexServlet.java",
    "framework/src/main/java/org/tron/core/services/http/BroadcastServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CancelAllUnfreezeV2Servlet.java",
    "framework/src/main/java/org/tron/core/services/http/ClearABIServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateAccountServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateAssetIssueServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateCommonTransactionServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateShieldNullifierServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateShieldedContractParametersServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateShieldedContractParametersWithoutAskServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateShieldedTransactionServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateShieldedTransactionWithoutSpendAuthSigServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateSpendAuthSigServlet.java",
    "framework/src/main/java/org/tron/core/services/http/CreateWitnessServlet.java",
    "framework/src/main/java/org/tron/core/services/http/DelegateResourceServlet.java",
    "framework/src/main/java/org/tron/core/services/http/DeployContractServlet.java",
    "framework/src/main/java/org/tron/core/services/http/EstimateEnergyServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ExchangeCreateServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ExchangeInjectServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ExchangeTransactionServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ExchangeWithdrawServlet.java",
    "framework/src/main/java/org/tron/core/services/http/FreezeBalanceServlet.java",
    "framework/src/main/java/org/tron/core/services/http/FreezeBalanceV2Servlet.java",
    "framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java",
    "framework/src/main/java/org/tron/core/services/http/GetAccountBalanceServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAccountByIdServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAccountNetServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAccountResourceServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAccountServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAkFromAskServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAssetIssueByAccountServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAssetIssueByIdServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAssetIssueByNameServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAssetIssueListByNameServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAssetIssueListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetAvailableUnfreezeCountServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBandwidthPricesServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBlockBalanceServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBlockByIdServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBlockByLatestNumServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBlockByLimitNextServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBlockByNumServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBlockServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBrokerageServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetBurnTrxServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetCanDelegatedMaxSizeServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetCanWithdrawUnfreezeAmountServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetChainParametersServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetContractInfoServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetContractServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceV2Servlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetDiversifierServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetEnergyPricesServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetExchangeByIdServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetExpandedSpendingKeyServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetIncomingViewingKeyServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetMarketOrderByAccountServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetMarketOrderByIdServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetMarketOrderListByPairServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetMarketPairListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetMarketPriceByPairServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetMemoFeePricesServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetMerkleTreeVoucherInfoServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetNewShieldedAddressServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetNextMaintenanceTimeServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetNkFromNskServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetNodeInfoServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetNowBlockServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedExchangeListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedNowWitnessListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPaginatedProposalListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetPendingSizeServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetProposalByIdServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetRcmServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetRewardServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetShieldTransactionHashServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetSpendingKeyServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionApprovedListServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionByIdServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionCountByBlockNumServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionFromPendingServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionInfoByBlockNumServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionInfoByIdServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionListFromPendingServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionReceiptByIdServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTransactionSignWeightServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetTriggerInputForShieldedTRC20ContractServlet.java",
    "framework/src/main/java/org/tron/core/services/http/GetZenPaymentAddressServlet.java",
    "framework/src/main/java/org/tron/core/services/http/HttpSelfFormatFieldName.java",
    "framework/src/main/java/org/tron/core/services/http/IsShieldedTRC20ContractNoteSpentServlet.java",
    "framework/src/main/java/org/tron/core/services/http/IsSpendServlet.java",
    "framework/src/main/java/org/tron/core/services/http/JsonFormat.java",
    "framework/src/main/java/org/tron/core/services/http/ListExchangesServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ListNodesServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ListProposalsServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ListWitnessesServlet.java",
    "framework/src/main/java/org/tron/core/services/http/MarketCancelOrderServlet.java",
    "framework/src/main/java/org/tron/core/services/http/MarketSellAssetServlet.java",
    "framework/src/main/java/org/tron/core/services/http/MetricsServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ParticipateAssetIssueServlet.java",
    "framework/src/main/java/org/tron/core/services/http/PostParams.java",
    "framework/src/main/java/org/tron/core/services/http/ProposalApproveServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ProposalCreateServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ProposalDeleteServlet.java",
    "framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ScanAndMarkNoteByIvkServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ScanNoteByIvkServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ScanNoteByOvkServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ScanShieldedTRC20NotesByIvkServlet.java",
    "framework/src/main/java/org/tron/core/services/http/ScanShieldedTRC20NotesByOvkServlet.java",
    "framework/src/main/java/org/tron/core/services/http/SetAccountIdServlet.java",
    "framework/src/main/java/org/tron/core/services/http/TotalTransactionServlet.java",
    "framework/src/main/java/org/tron/core/services/http/TransferAssetServlet.java",
    "framework/src/main/java/org/tron/core/services/http/TransferServlet.java",
    "framework/src/main/java/org/tron/core/services/http/TriggerConstantContractServlet.java",
    "framework/src/main/java/org/tron/core/services/http/TriggerSmartContractServlet.java",
    "framework/src/main/java/org/tron/core/services/http/UnDelegateResourceServlet.java",
    "framework/src/main/java/org/tron/core/services/http/UnFreezeAssetServlet.java",
    "framework/src/main/java/org/tron/core/services/http/UnFreezeBalanceServlet.java",
    "framework/src/main/java/org/tron/core/services/http/UnFreezeBalanceV2Servlet.java",
    "framework/src/main/java/org/tron/core/services/http/UpdateAccountServlet.java",
    "framework/src/main/java/org/tron/core/services/http/UpdateAssetServlet.java",
    "framework/src/main/java/org/tron/core/services/http/UpdateBrokerageServlet.java",
    "framework/src/main/java/org/tron/core/services/http/UpdateEnergyLimitServlet.java",
    "framework/src/main/java/org/tron/core/services/http/UpdateSettingServlet.java",
    "framework/src/main/java/org/tron/core/services/http/UpdateWitnessServlet.java",
    "framework/src/main/java/org/tron/core/services/http/Util.java",
    "framework/src/main/java/org/tron/core/services/http/ValidateAddressServlet.java",
    "framework/src/main/java/org/tron/core/services/http/VoteWitnessAccountServlet.java",
    "framework/src/main/java/org/tron/core/services/http/WithdrawBalanceServlet.java",
    "framework/src/main/java/org/tron/core/services/http/WithdrawExpireUnfreezeServlet.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcErrorResolver.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpc.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/BlockFilterAndResult.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/FilterResult.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogBlockQuery.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilterAndResult.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilterWrapper.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/interceptor/MetricInterceptor.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/BlockResult.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/BuildArguments.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/CallArguments.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionReceipt.java",
    "framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionResult.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/PrometheusInterceptor.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterContainer.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/RpcApiAccessInterceptor.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/DefaultBaseQqsAdapter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/GlobalPreemptibleAdapter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/IPQPSRateLimiterAdapter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/IPreemptibleRateLimiter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/IRateLimiter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/adapter/QpsRateLimiterAdapter.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/strategy/GlobalPreemptibleStrategy.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/strategy/IPQpsStrategy.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/strategy/QpsStrategy.java",
    "framework/src/main/java/org/tron/core/services/ratelimiter/strategy/Strategy.java",
    "framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java",
    "framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java",
    "framework/src/main/java/org/tron/core/zen/ZksnarkInitService.java",
    "framework/src/main/java/org/tron/core/zen/address/DiversifierT.java",
    "framework/src/main/java/org/tron/core/zen/address/ExpandedSpendingKey.java",
    "framework/src/main/java/org/tron/core/zen/address/FullViewingKey.java",
    "framework/src/main/java/org/tron/core/zen/address/IncomingViewingKey.java",
    "framework/src/main/java/org/tron/core/zen/address/KeyIo.java",
    "framework/src/main/java/org/tron/core/zen/address/PaymentAddress.java",
    "framework/src/main/java/org/tron/core/zen/address/SpendingKey.java",
    "framework/src/main/java/org/tron/core/zen/note/Note.java",
    "framework/src/main/java/org/tron/core/zen/note/NoteEncryption.java",
    "framework/src/main/java/org/tron/core/zen/note/OutgoingPlaintext.java",
]


target_scopes = [
    "Critical. An unprivileged attacker can bypass signature recovery, permission-id, multisig threshold, operations mask, account ownership, or transaction authorization checks and execute transfers, contract calls, votes, or account changes as another user.",
    "Critical. An unprivileged attacker can steal, mint, duplicate, unlock, withdraw, redirect, or misaccount TRX, TRC10, TRC20, shielded value, delegated resources, exchange liquidity, market inventory, or rewards they do not own.",
    "Critical. An unprivileged attacker can replay or double-apply a transaction, approval, withdrawal, market fill, exchange settlement, reward claim, shielded spend, nullifier, or resource transition so one logical action settles more than once.",
    "Critical. An unprivileged attacker can submit a crafted transaction, contract bytecode, or RPC input that makes honest java-tron nodes commit invalid state, diverge on TVM execution or accounting, or deterministically halt on public chain input.",
    "High. An unprivileged attacker can permanently lock user funds, delegated resources, market orders, exchange balances, shielded notes, rewards, or withdrawals by breaking cleanup, settlement ordering, note handling, nullifier handling, or beneficiary accounting.",
    "High. An unprivileged attacker can use public HTTP, gRPC, JSON-RPC, estimate/call paths, or transaction execution to trigger materially underpriced CPU, memory, disk, or state-iteration work and degrade or crash production nodes below true cost.",
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
    This repo defines java-tron full-node transaction execution, account permissions, TVM execution, native contracts, market/exchange settlement, delegated resources, shielded transfers, wallet entrypoints, and public HTTP/gRPC/JSON-RPC surfaces. The main bounty focus is bugs that compromise intended node behaviour, especially auth, accounting, one-time settlement, deterministic execution, and public resource-cost controls.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Java symbols when possible.
    * Attacker is unprivileged only: a normal external user using transaction broadcast, contract deploy/call, shielded inputs, or public HTTP/gRPC/JSON-RPC requests.
    * Never assume admin, governance, witness/SR control, node/peer control, local keystore access, or leaked keys.
    * Do not rely on mocked paths, handcrafted internal helpers, direct storage writes, or off-repo infrastructure assumptions.
    * Generate 12 to 18 high-signal questions.
    * At least 70% must be multi-step auth, accounting, replay, settlement, deterministic-execution, or public-cost questions.
    * Every question must be testable by unit test, integration test, fuzz test, invariant test, or differential test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * No unprivileged user can authorize actions for another account or bypass permission masks or signer thresholds.
    * No unprivileged user can mint, unlock, move, or burn value they do not control.
    * One logical spend, withdrawal, claim, fill, vote, or nullifier must settle exactly once.
    * TVM, precompiles, fees, refunds, and state commits must stay deterministic across honest nodes for the same public input.
    * Public HTTP/gRPC/JSON-RPC and estimate/call paths must not bypass validation or expose materially underpriced work.
    * Crafted but valid user input must not permanently halt production flows or freeze user funds.

    Each question must include:
    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: test/fuzz PARAMETERS and assert AUTH, ACCOUNTING, ONE_TIME_SETTLEMENT, or DETERMINISTIC_EXECUTION_PROPERTY.",
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
- Attacker is unprivileged only: real transaction, contract, shielded, or public API input.
- Reject admin/governance/node/peer/leaked-key/local-keystore/mocked-path/direct-storage/off-repo/best-practice issues.

## Validate
- Trace the exact reachable Java path and attacker entrypoint.
- Check whether auth, accounting, replay, settlement, fee, or cost guards already stop it.
- Accept only real unauthorized execution, value theft/duplication/freeze, replay/double-settlement, invalid state/divergence/halt, or materially underpriced public work.
- Require exact file/function support and a reproducible Java unit/integration/fuzz/invariant PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact]

### Likelihood Explanation
[Preconditions, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Java unit/integration test or fuzz/invariant test plan with expected assertions]

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
- Reject admin-only, governance-only, node-only, peer-only, leaked-key, local-keystore, best-practice, docs/style, gas-only, mocked-path, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, direct storage mutation, mocked paths, or unsupported protocol behavior.
- A valid report must be triggerable by an unprivileged user, unless the claim proves privilege escalation from a user path.
- The final impact must match an in-scope bounty impact for java-tron production code, not just a generic code bug.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security/accounting assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood.
6. Reproducible proof path: unit PoC, fork test, invariant/fuzz test, or exact manual steps.
7. No obvious rejection reason from SECURITY.md, known issues, privileges, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user trigger this through a real transaction, contract, shielded, or public API path?
- Does the code actually behave as claimed?
- Is the impact caused by the code, not by a malicious node, peer, or external dependency alone?
- Is the loss/freeze/insolvency concrete, not hypothetical?
- Would a bounty triager accept the proof?
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
[Concrete in-scope impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/fork test plan]

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
- Keep only unprivileged-user analogs in actuators, TVM, wallet/API, market/exchange, resources, shielded flows, or state/accounting code.
- Reject trusted-role, mocked/internal-only, theoretical-only, or no-impact analogs.

## Validate
- Map the bug class to the strongest reachable java-tron path.
- Prove root cause with exact file/function support.
- Accept only concrete auth, accounting, replay, settlement, invalid-state/divergence/halt, or underpriced-public-work impact.

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
