### Title
Uncaught-exception `printStackTrace()` calls on transaction-parsing/actuator paths leak stack traces and can mask failures without proper logging - (File: `actuator/src/main/java/org/tron/core/actuator/ActuatorFactory.java`, `framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java`)

### Summary
The report's bug class ("debug artifacts leaking data / uncontrolled diagnostic output") maps to `e.printStackTrace()` calls left in production code paths that process untrusted, attacker-supplied transaction/contract data reachable from broadcast transactions and JSON-RPC calls.

### Finding Description
`ActuatorFactory.createActuator()` builds the actuator list for every contract in a broadcast transaction; on `IllegalAccessException`/`InstantiationException` it calls `e.printStackTrace()` instead of using the class's own `@Slf4j(topic = "actuator") logger` [1](#0-0) . Similarly, `JsonRpcApiUtil.getTo()`, which unpacks arbitrary `Transaction.Contract` payloads reachable from JSON-RPC transaction/block queries, catches `Exception` and calls `ex.printStackTrace()` rather than logging through the class's `@Slf4j(topic = "API") logger` used elsewhere in the same file [2](#0-1) . A third instance in `getAmountFromTransactionInfo()` catches `Throwable t` and calls `t.printStackTrace()` [3](#0-2) . Because these code paths process attacker-controlled `Any`-packed contract parameters unpacked via `contractParameter.unpack(...)`, malformed or type-confusing input can be crafted to throw exceptions and trigger these `printStackTrace()` calls on every request, writing directly to the node's stdout/stderr outside the structured `slf4j` logging pipeline used elsewhere in these classes.

### Impact Explanation
`printStackTrace()` output bypasses log level control, redaction, and log rotation/retention policies configured for the structured logger, and can be repeatedly triggered by any anonymous peer submitting a malformed broadcast transaction or JSON-RPC call, causing unbounded, uncontrolled stdout writes (log/disk exhaustion risk) and inconsistent/incomplete diagnostics compared to the rest of the codebase, which is a Logging and Monitoring Failure (OWASP A09:2021) consistent with the report's bug class. This does not by itself leak private keys or directly grant account takeover, so the impact is bounded to log integrity/availability and diagnostic inconsistency, not consensus divergence or fund loss.

### Likelihood Explanation
High reachability: `ActuatorFactory.createActuator()` runs for every contract in every processed transaction, and `JsonRpcApiUtil.getTo()`/`getAmountFromTransactionInfo()` run on JSON-RPC transaction/block queries — both paths are reachable from anonymous broadcast transactions or unauthenticated JSON-RPC requests without any special privilege.

### Recommendation
Replace all `printStackTrace()` calls in `ActuatorFactory.java` and `JsonRpcApiUtil.java` with the existing `slf4j` `logger.warn`/`logger.error` calls used elsewhere in the same classes (e.g., matching the pattern already used at `JsonRpcApiUtil.java` lines 239-242, 316-319, 360-362 with `Throwables.getStackTraceAsString(e)`), so output is routed through structured, level-controlled logging.

### Proof of Concept
Send a broadcast transaction whose contract type maps to a class that cannot be instantiated (or a JSON-RPC call whose transaction contains a malformed `Any` payload for `getTo()`/`getAmountFromTransactionInfo()`); the exception path executes `printStackTrace()` at [4](#0-3)  or [5](#0-4) , writing directly to stdout on every such request, independent of configured log levels.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ActuatorFactory.java (L38-45)
```java
        .forEach(contract -> {
          try {
            actuatorList
                .add(getActuatorByContract(contract, chainBaseManager, transactionCapsule));
          } catch (IllegalAccessException | InstantiationException e) {
            e.printStackTrace();
          }
        });
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java (L134-214)
```java
  public static List<ByteString> getTo(Transaction transaction) {
    Transaction.Contract contract = transaction.getRawData().getContract(0);
    List<ByteString> list = new ArrayList<>();
    try {
      Any contractParameter = contract.getParameter();
      switch (contract.getType()) {
        case AccountCreateContract:
          list.add(contractParameter.unpack(AccountCreateContract.class).getAccountAddress());
          break;
        case TransferContract:
          list.add(contractParameter.unpack(TransferContract.class).getToAddress());
          break;
        case TransferAssetContract:
          list.add(contractParameter.unpack(TransferAssetContract.class).getToAddress());
          break;
        case VoteAssetContract:
          list.addAll(contractParameter.unpack(VoteAssetContract.class).getVoteAddressList());
          break;
        case VoteWitnessContract:
          for (Vote vote : contractParameter.unpack(VoteWitnessContract.class).getVotesList()) {
            list.add(vote.getVoteAddress());
          }
          break;
        case ParticipateAssetIssueContract:
          list.add(contractParameter.unpack(ParticipateAssetIssueContract.class).getToAddress());
          break;
        case FreezeBalanceContract:
          ByteString receiverAddress = contractParameter.unpack(FreezeBalanceContract.class)
              .getReceiverAddress();
          if (!receiverAddress.isEmpty()) {
            list.add(receiverAddress);
          }
          break;
        case UnfreezeBalanceContract:
          receiverAddress = contractParameter.unpack(UnfreezeBalanceContract.class)
              .getReceiverAddress();
          if (!receiverAddress.isEmpty()) {
            list.add(receiverAddress);
          }
          break;
        case DelegateResourceContract:
          receiverAddress = contractParameter.unpack(DelegateResourceContract.class)
                  .getReceiverAddress();
          if (!receiverAddress.isEmpty()) {
            list.add(receiverAddress);
          }
          break;
        case UnDelegateResourceContract:
          receiverAddress = contractParameter.unpack(UnDelegateResourceContract.class)
                  .getReceiverAddress();
          if (!receiverAddress.isEmpty()) {
            list.add(receiverAddress);
          }
          break;
        case TriggerSmartContract:
          list.add(contractParameter.unpack(TriggerSmartContract.class).getContractAddress());
          break;
        case UpdateSettingContract:
          list.add(contractParameter.unpack(UpdateSettingContract.class).getContractAddress());
          break;
        case UpdateEnergyLimitContract:
          list.add(contractParameter.unpack(UpdateEnergyLimitContract.class).getContractAddress());
          break;
        case ClearABIContract:
          list.add(contractParameter.unpack(ClearABIContract.class).getContractAddress());
          break;
        case ShieldedTransferContract:
          ShieldedTransferContract shieldedTransferContract = contract.getParameter()
              .unpack(ShieldedTransferContract.class);
          if (!shieldedTransferContract.getTransparentToAddress().isEmpty()) {
            list.add(shieldedTransferContract.getTransparentToAddress());
          }
          break;
        default:
          break;
      }
      return list;
    } catch (Exception ex) {
      ex.printStackTrace();
    }
    return list;
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java (L360-365)
```java
    } catch (Exception e) {
      logger.warn("Exception happens when get amount from transactionInfo. Exception = [{}]",
          Throwables.getStackTraceAsString(e));
    } catch (Throwable t) {
      t.printStackTrace();
    }
```
