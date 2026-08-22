### Title
`ForbidTransferToContract` compliance/emergency-control restriction is bypassed by TVM call-value/token-value transfers - ([File: actuator/src/main/java/org/tron/core/vm/VMUtils.java])

### Summary
Mirroring the reported bug class (a transfer-time policy check enforced on the "normal" money-movement path but skipped by an alternate value-movement mechanism), java-tron enforces the `ForbidTransferToContract` dynamic-property restriction only inside the ordinary `TransferActuator`/`TransferAssetActuator` validation paths, but never inside the TVM's own internal value/TRC10-token transfer path used by `TriggerSmartContract` execution. Anyone can move TRX or any TRC10 asset into a smart-contract account through a plain contract call, completely bypassing the flag that governance uses to stop exactly that.

### Finding Description
`ForbidTransferToContract` is a governance-controlled dynamic property intended to prevent TRX/TRC10 assets from being sent into smart-contract accounts. It is checked explicitly in the two "normal transfer" actuators: [1](#0-0) 

and analogously in `TransferActuator` for plain TRX transfers (also grepped to reference `getForbidTransferToContract`).

However, when a value/TRC10 token amount is attached to a `TriggerSmartContract` call (`call_value` / `call_token_value`), the VM actuator moves the funds directly via `MUtil.transfer` / `MUtil.transferToken`: [2](#0-1) 

and for contract-creation calls: [3](#0-2) 

Those helpers delegate to `VMUtils.validateForSmartContract`, which performs its own, independent set of checks (address validity, self-transfer, balance sufficiency, asset existence, overflow) but has **no** reference to `ForbidTransferToContract` at all: [4](#0-3) [5](#0-4) 

The same `validateForSmartContract`/`MUtil` pair is also used for internal `CALL`/precompiled-call/`SUICIDE` value transfers inside contract execution: [6](#0-5) [7](#0-6) 

So, exactly as in the report — where the source-chain `TransferHook`/Policy Engine only guards the "normal" transfer instruction while the bridge's burn/remint path moves value through a separate, unguarded mechanism — java-tron's `ForbidTransferToContract` control only guards the "normal" `TransferContract`/`TransferAssetContract` instructions while the TVM's call-value/token-value transfer mechanism moves the exact same assets into contract accounts through a code path that was never wired up to the restriction.

### Impact Explanation
This weakens the protocol's own emergency-control assumption around `ForbidTransferToContract`. If the Committee activates this flag (e.g., to block a class of transfers to contract addresses for compliance/security reasons), any unprivileged user can still route TRX or any TRC10 asset into a contract account simply by broadcasting a `TriggerSmartContract` transaction with `call_value`/`call_token_value` set (or by having a contract perform an internal `CALL` carrying value/token value). This is a real state/accounting-level bypass of a documented protocol safety switch, reachable from a plain, anonymous broadcast transaction — not a privileged-actor or leaked-key scenario.

### Likelihood Explanation
High. No special privileges, keys, or peer/node compromise are required — any account can construct and broadcast a `TriggerSmartContract` transaction (or deploy a trivial contract that forwards value) with a nonzero `call_value`/`call_token_value` targeting any deployed contract, which is a routine, everyday operation on TRON already supported by `VMConfig.allowTvmTransferTrc10()`.

### Recommendation
Add the same `ForbidTransferToContract` (and any other transfer-actuator-level compliance checks intended to apply universally) check inside `VMUtils.validateForSmartContract`, or otherwise gate `MUtil.transfer`/`MUtil.transferToken` so that TVM call-value/token-value transfers into contract accounts are also rejected when the flag is active, closing the gap between the "normal" transfer actuators and the VM's internal value-movement path.

### Proof of Concept
1. Assume the Committee sets `ForbidTransferToContract = 1` via a `ProposalApproveContract`, so `dynamicStore.getForbidTransferToContract() == 1`.
2. Confirm a direct `TransferAssetContract` sending TRC10 tokens to a contract address now fails with `"Cannot transfer asset to smartContract."` per `TransferAssetActuator.validate()` (lines 172-175).
3. Instead, broadcast a `TriggerSmartContract` transaction targeting that same contract address with `call_token_value` (and/or `call_value`) set to a nonzero amount.
4. Observe that `VMActuator.call()` (lines 551-560) unconditionally calls `MUtil.transfer`/`MUtil.transferToken`, which only run `VMUtils.validateForSmartContract` — a method that never inspects `ForbidTransferToContract` — so the TRC10/TRX balance is moved into the contract account despite the active restriction.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L169-175)
```java
    AccountCapsule toAccount = accountStore.get(toAddress);
    if (toAccount != null) {
      //after ForbidTransferToContract proposal, send trx to smartContract by actuator is not allowed.
      if (dynamicStore.getForbidTransferToContract() == 1
          && toAccount.getType() == AccountType.Contract) {
        throw new ContractValidateException("Cannot transfer asset to smartContract.");
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L447-454)
```java
    // transfer from callerAddress to contractAddress according to callValue
    if (callValue > 0) {
      MUtil.transfer(rootRepository, callerAddress, contractAddress, callValue);
    }
    if (VMConfig.allowTvmTransferTrc10() && tokenValue > 0) {
      MUtil.transferToken(rootRepository, callerAddress, contractAddress, String.valueOf(tokenId),
          tokenValue);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L551-560)
```java
    program.getResult().setContractAddress(contractAddress);
    //transfer from callerAddress to targetAddress according to callValue

    if (callValue > 0) {
      MUtil.transfer(rootRepository, callerAddress, contractAddress, callValue);
    }
    if (VMConfig.allowTvmTransferTrc10() && tokenValue > 0) {
      MUtil.transferToken(rootRepository, callerAddress, contractAddress, String.valueOf(tokenId),
          tokenValue);
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/VMUtils.java (L182-247)
```java
  public static boolean validateForSmartContract(Repository deposit, byte[] ownerAddress,
      byte[] toAddress, byte[] tokenId, long amount) throws ContractValidateException {
    if (deposit == null) {
      throw new ContractValidateException("No deposit!");
    }

    byte[] tokenIdWithoutLeadingZero = ByteUtil.stripLeadingZeroes(tokenId);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid ownerAddress");
    }
    if (!DecodeUtil.addressValid(toAddress)) {
      throw new ContractValidateException("Invalid toAddress");
    }

    if (amount <= 0) {
      throw new ContractValidateException("Amount must greater than 0.");
    }

    if (Arrays.equals(ownerAddress, toAddress)) {
      throw new ContractValidateException("Cannot transfer asset to yourself.");
    }

    AccountCapsule ownerAccount = deposit.getAccount(ownerAddress);
    if (ownerAccount == null) {
      throw new ContractValidateException("No owner account!");
    }

    if (deposit.getAssetIssue(tokenIdWithoutLeadingZero) == null) {
      throw new ContractValidateException("No asset !");
    }
    if (!Commons.getAssetIssueStoreFinal(deposit.getDynamicPropertiesStore(),
        deposit.getAssetIssueStore(), deposit.getAssetIssueV2Store())
        .has(tokenIdWithoutLeadingZero)) {
      throw new ContractValidateException("No asset !");
    }

    Long assetBalance = ownerAccount.getAsset(deposit.getDynamicPropertiesStore(),
            ByteArray.toStr(tokenIdWithoutLeadingZero));
    if (null == assetBalance || assetBalance <= 0) {
      throw new ContractValidateException("assetBalance must greater than 0.");
    }
    if (amount > assetBalance) {
      throw new ContractValidateException("assetBalance is not sufficient.");
    }

    AccountCapsule toAccount = deposit.getAccount(toAddress);
    if (toAccount != null) {
      assetBalance = toAccount.getAsset(deposit.getDynamicPropertiesStore(),
              ByteArray.toStr(tokenIdWithoutLeadingZero));
      if (assetBalance != null) {
        try {
          addExact(assetBalance, amount,
              VMConfig.disableJavaLangMath()); //check if overflow
        } catch (Exception e) {
          logger.debug(e.getMessage(), e);
          throw new ContractValidateException(e.getMessage());
        }
      }
    } else {
      throw new ContractValidateException(
          "Validate InternalTransfer error, no ToAccount. And not allowed to create account in smart contract.");
    }

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/MUtil.java (L18-52)
```java
  public static void transfer(Repository deposit, byte[] fromAddress, byte[] toAddress, long amount)
      throws ContractValidateException {
    if (0 == amount) {
      return;
    }
    VMUtils.validateForSmartContract(deposit, fromAddress, toAddress, amount);
    deposit.addBalance(toAddress, amount);
    deposit.addBalance(fromAddress, -amount);
  }

  public static void transferAllToken(Repository deposit, byte[] fromAddress, byte[] toAddress) {
    AccountCapsule fromAccountCap = deposit.getAccount(fromAddress);
    Protocol.Account.Builder fromBuilder = fromAccountCap.getInstance().toBuilder();
    AccountCapsule toAccountCap = deposit.getAccount(toAddress);
    toAccountCap.importAllAsset();
    Protocol.Account.Builder toBuilder = toAccountCap.getInstance().toBuilder();
    fromAccountCap.getAssetMapV2().forEach((tokenId, amount) -> {
      toBuilder.putAssetV2(tokenId, toBuilder.getAssetV2Map().getOrDefault(tokenId, 0L) + amount);
      fromBuilder.putAssetV2(tokenId, 0L);
    });

    deposit.putAccountValue(fromAddress, new AccountCapsule(fromBuilder.build()));
    deposit.putAccountValue(toAddress, new AccountCapsule(toBuilder.build()));
  }

  public static void transferToken(Repository deposit, byte[] fromAddress, byte[] toAddress,
      String tokenId, long amount)
      throws ContractValidateException {
    if (0 == amount) {
      return;
    }
    VMUtils.validateForSmartContract(deposit, fromAddress, toAddress, tokenId.getBytes(), amount);
    deposit.addTokenBalance(toAddress, tokenId.getBytes(), amount);
    deposit.addTokenBalance(fromAddress, tokenId.getBytes(), -amount);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1081-1110)
```java
    } else if (!ArrayUtils.isEmpty(senderAddress) && !ArrayUtils.isEmpty(contextAddress)
        && senderAddress != contextAddress && endowment > 0) {
      createAccountIfNotExist(deposit, contextAddress);
      if (!isTokenTransfer) {
        try {
          VMUtils
              .validateForSmartContract(deposit, senderAddress, contextAddress, endowment);
        } catch (ContractValidateException e) {
          if (VMConfig.allowTvmConstantinople()) {
            refundEnergy(msg.getEnergy().longValue(), REFUND_ENERGY_FROM_MESSAGE_CALL);
            throw new TransferException("transfer trx failed: %s", e.getMessage());
          }
          throw new BytecodeExecutionException(VALIDATE_FOR_SMART_CONTRACT_FAILURE, e.getMessage());
        }
        deposit.addBalance(senderAddress, -endowment);
        contextBalance = deposit.addBalance(contextAddress, endowment);
      } else {
        try {
          VMUtils.validateForSmartContract(deposit, senderAddress, contextAddress,
              tokenId, endowment);
        } catch (ContractValidateException e) {
          if (VMConfig.allowTvmConstantinople()) {
            refundEnergy(msg.getEnergy().longValue(), REFUND_ENERGY_FROM_MESSAGE_CALL);
            throw new TransferException("transfer trc10 failed: %s", e.getMessage());
          }
          throw new BytecodeExecutionException(VALIDATE_FOR_SMART_CONTRACT_FAILURE, e.getMessage());
        }
        deposit.addTokenBalance(senderAddress, tokenId, -endowment);
        deposit.addTokenBalance(contextAddress, tokenId, endowment);
      }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1702-1721)
```java
    if (!ArrayUtils.isEmpty(senderAddress) && !ArrayUtils.isEmpty(contextAddress)
        && senderAddress != contextAddress && msg.getEndowment().value().longValueExact() > 0) {
      if (!isTokenTransfer) {
        try {
          MUtil.transfer(deposit, senderAddress, contextAddress,
              msg.getEndowment().value().longValueExact());
        } catch (ContractValidateException e) {
          throw new BytecodeExecutionException("transfer failure");
        }
      } else {
        try {
          VMUtils
              .validateForSmartContract(deposit, senderAddress, contextAddress, tokenId, endowment);
        } catch (ContractValidateException e) {
          throw new BytecodeExecutionException(VALIDATE_FOR_SMART_CONTRACT_FAILURE, e.getMessage());
        }
        deposit.addTokenBalance(senderAddress, tokenId, -endowment);
        deposit.addTokenBalance(contextAddress, tokenId, endowment);
      }
    }
```
