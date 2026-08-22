I found the key mechanism confirming the analog is valid and exploitable in java-tron.

### Title
Account can shed its "Contract" designation via selfdestruct + re-fund, bypassing contract-address restrictions in TransferActuator/DelegateResourceActuator - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
Multiple java-tron actuators gate privileged behavior on the persisted `AccountCapsule.getType() == AccountType.Contract` flag (java-tron's on-chain analog of Solidity's `isContract()`/`extcodesize` check from the Blocklist.sol report). Because `SUICIDE`/`SELFDESTRUCT` fully deletes the account record (`getResult().addDeleteAccount(...)`), and a subsequent plain TRX transfer recreates the address as a fresh `AccountType.Normal` account, an attacker can erase the on-chain evidence that an address is a smart contract and make actuators treat a former contract address as an ordinary EOA.

### Finding Description
- `Program.suicide()`/`suicide2()` deletes the contract's account entirely via `getResult().addDeleteAccount(this.getContractAddress())` [1](#0-0)  and the deletion is confirmed by tests asserting `accountStore.get(contractAddr)` is `null` after suicide [2](#0-1) .
- `TransferActuator.execute()` recreates a non-existent destination address as a brand-new `AccountType.Normal` account whenever it receives a plain TRX transfer [3](#0-2) .
- Several actuators use the persisted `AccountType.Contract` flag as a security/business-logic gate that assumes the flag reliably reflects whether an address is a smart contract:
  - `TransferActuator.validate()` forbids sending TRX to a `AccountType.Contract` address when `ForbidTransferToContract` is enabled [4](#0-3) .
  - `DelegateResourceActuator.validate()` forbids delegating bandwidth/energy resources to a `receiverCapsule.getType() == AccountType.Contract` address [5](#0-4) .
  - `FreezeBalanceActuator.validate()` similarly blocks delegating resources to a contract-typed receiver [6](#0-5) .

Sequence to bypass:
1. Attacker deploys a contract at address `X` (e.g., via `CREATE2` so the address is precomputable/reproducible), which is recorded with `AccountType.Contract`.
2. Attacker triggers `SUICIDE`/`SELFDESTRUCT` on `X`, which fully removes the `AccountCapsule` for `X` from the account store [7](#0-6) .
3. Anyone (attacker or a colluding party) sends a plain `TransferContract` (TRX transfer) to `X`. Because the account no longer exists, this recreates `X` as a plain `AccountType.Normal` account [3](#0-2) .
4. Now `X` passes every `AccountType.Contract`-based check (`ForbidTransferToContract` in `TransferActuator`, the contract-receiver block in `DelegateResourceActuator`/`FreezeBalanceActuator`), even though `X` is the exact same address that recently ran attacker-controlled contract code and could later run contract code again (subject to the separate `createContractImpl` existing-account check).

This is the direct java-tron analog of the Blocklist.sol issue: a persisted "is this a contract" flag can be reset by the address owner (via selfdestruct) and then have its state re-established as a non-contract, undermining any restriction predicated on that flag.

### Impact Explanation
An attacker can bypass on-chain restrictions specifically designed to protect resource-delegation and TRX-transfer safety for contract accounts (`ForbidTransferToContract`, "do not delegate to contract addresses"). This can be used to funnel delegated bandwidth/energy or TRX into what the protocol believes is a plain EOA, when in fact the address is/was contract-controlled, undermining the intended separation between EOA and contract accounting paths. This is a real business-logic bypass but the practical value is limited by the fact that redeploying an actual contract back on top of that "laundered" address is blocked by the existing-account check in `VMActuator`/`Program.createContractImpl` (`contractAlreadyExists`), so the attacker cannot simultaneously hold both the bypass and live contract code at the same address without additional protocol-level assistance.

### Likelihood Explanation
Reachable entirely with unprivileged, ordinary broadcast transactions: `TriggerSmartContract` (to deploy/selfdestruct) and `TransferContract`/`DelegateResourceContract` (to exploit the now-Normal account). No special privileges, keys, or malicious peers required.

### Recommendation
Do not rely solely on the mutable `AccountType` field to gate financial/resource restrictions tied to "is this address a contract." Consider either: (a) never fully clearing account metadata on `SUICIDE` (keep a persistent "was a contract" marker, mirroring EIP-6780-style restrictions already partially present via `allowTvmSelfdestructRestriction`), or (b) re-deriving contract status at decision time from the presence of code/`ContractStore` entries rather than a value that can be reset by deleting and recreating the account.

### Proof of Concept
1. Deploy contract `X` via `CreateSmartContractContract`/`CREATE2` (see `Create2Test`/`FreezeTest.sol` `deployCreate2Contract`).
2. Broadcast a `TriggerSmartContract` invoking `SELFDESTRUCT` on `X` — the account is deleted (`Program.suicide`, confirmed by `ProgramResultTest.suicideResultTest` asserting `accountStore.get(suicideContract)` is `null`).
3. Broadcast a `TransferContract` sending TRX to `X` — `TransferActuator.execute()` recreates `X` as `AccountType.Normal`.
4. Broadcast a `DelegateResourceContract` (or a TRX transfer under `ForbidTransferToContract`) targeting `X` as receiver — `DelegateResourceActuator.validate()`'s `receiverCapsule.getType() == AccountType.Contract` check now passes because `X` is `Normal`, even though `X` was, moments earlier, an attacker-deployed smart contract at that same address.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L451-516)
```java
  public void suicide(DataWord obtainerAddress) {

    byte[] owner = getContextAddress();
    byte[] obtainer = obtainerAddress.toTronAddress();

    if (VMConfig.allowTvmVote()) {
      withdrawRewardAndCancelVote(owner, getContractState());
    }

    long balance = getContractState().getBalance(owner);

    if (logger.isDebugEnabled()) {
      logger.debug("Transfer to: [{}] heritage: [{}]",
          Hex.toHexString(obtainer),
          balance);
    }

    increaseNonce();

    InternalTransaction internalTx = addInternalTx(null, owner, obtainer, balance, null,
        "suicide", nonce, getContractState().getAccount(owner).getAssetMapV2());

    int ADDRESS_SIZE = VMUtils.getAddressSize();
    if (FastByteComparisons.compareTo(owner, 0, ADDRESS_SIZE, obtainer, 0, ADDRESS_SIZE) == 0) {
      // if owner == obtainer just zeroing account according to Yellow Paper
      getContractState().addBalance(owner, -balance);
      byte[] blackHoleAddress = getContractState().getBlackHoleAddress();
      if (VMConfig.allowTvmTransferTrc10()) {
        getContractState().addBalance(blackHoleAddress, balance);
        MUtil.transferAllToken(getContractState(), owner, blackHoleAddress);
      }
    } else {
      createAccountIfNotExist(getContractState(), obtainer);
      try {
        MUtil.transfer(getContractState(), owner, obtainer, balance);
        if (VMConfig.allowTvmTransferTrc10()) {
          MUtil.transferAllToken(getContractState(), owner, obtainer);
        }
      } catch (ContractValidateException e) {
        if (VMConfig.allowTvmConstantinople()) {
          throw new TransferException(
              "transfer all token or transfer all trx failed in suicide: %s", e.getMessage());
        }
        throw new BytecodeExecutionException("transfer failure");
      }
    }
    if (VMConfig.allowTvmFreeze()) {
      byte[] blackHoleAddress = getContractState().getBlackHoleAddress();
      if (FastByteComparisons.isEqual(owner, obtainer)) {
        transferDelegatedResourceToInheritor(owner, blackHoleAddress, getContractState());
      } else {
        transferDelegatedResourceToInheritor(owner, obtainer, getContractState());
      }
    }
    if (VMConfig.allowTvmFreezeV2()) {
      byte[] Inheritor =
          FastByteComparisons.isEqual(owner, obtainer)
              ? getContractState().getBlackHoleAddress()
              : obtainer;
      long expireUnfrozenBalance = transferFrozenV2BalanceToInheritor(owner, Inheritor, getContractState());
      if (expireUnfrozenBalance > 0 && internalTx != null) {
        internalTx.setValue(internalTx.getValue() + expireUnfrozenBalance);
      }
    }
    getResult().addDeleteAccount(this.getContractAddress());
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferActuator.java (L48-58)
```java
      // if account with to_address does not exist, create it first.
      AccountCapsule toAccount = accountStore.get(toAddress);
      if (toAccount == null) {
        boolean withDefaultPermission =
            dynamicStore.getAllowMultiSign() == 1;
        toAccount = new AccountCapsule(ByteString.copyFrom(toAddress), AccountType.Normal,
            dynamicStore.getLatestBlockHeaderTimestamp(), withDefaultPermission, dynamicStore);
        accountStore.put(toAddress, toAccount);

        fee = fee + dynamicStore.getCreateNewAccountFeeInSystemContract();
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferActuator.java (L132-139)
```java
      //after ForbidTransferToContract proposal, send trx to smartContract by actuator is not allowed.
      if (dynamicStore.getForbidTransferToContract() == 1
          && toAccount != null
          && toAccount.getType() == AccountType.Contract) {

        throw new ContractValidateException("Cannot transfer TRX to a smartContract.");

      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L243-246)
```java
    if (receiverCapsule.getType() == AccountType.Contract) {
      throw new ContractValidateException(
          "Do not allow delegate resources to contract addresses");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L262-267)
```java
      if (dynamicStore.getAllowTvmConstantinople() == 1
          && receiverCapsule.getType() == AccountType.Contract) {
        throw new ContractValidateException(
            "Do not allow delegate resources to contract addresses");

      }
```
