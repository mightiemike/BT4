### Title
Incomplete state reset on CREATE2 address pre-funding allows frontrunning of contract deployment - (File: actuator/src/main/java/org/tron/core/vm/program/Program.java)

### Summary
When a smart contract is deployed via `CREATE`/`CREATE2`, java-tron checks whether an account already exists at the predicted target address and, if so, only partially resets that account's state before converting it into the new contract account. Specifically, `AccountCapsule.clearDelegatedResource()` clears only the *inbound* ("acquired") delegated resource balances, but leaves frozen balances, outbound delegated resources, delegated-resource store entries, and votes intact. This is the same bug class as the deposit-contract frontrunning issue: an unprivileged party can pre-set state at a predictable address before the "real"/larger operation (contract deployment) occurs, and that pre-set state survives and is trusted afterwards.

### Finding Description
`CREATE2` addresses are fully deterministic from `sender address + salt + code hash` via `WalletUtil.generateContractAddress2` [1](#0-0)  . Any external account can therefore predict the future contract address before it is deployed and interact with it in advance — e.g. by freezing TRX for bandwidth/energy at that address, delegating resources to or from that address, or voting through it — exactly as a malicious validator in the reported deposit-contract bug pre-sets withdrawal credentials before the legitimate 32 ETH deposit arrives.

When the contract is eventually deployed at that address, `createContractImpl` checks for an existing account and, if it is not already an actual contract, converts it in place: [2](#0-1) 

The only state cleanup performed is `existingAccount.clearDelegatedResource()`, which resets solely the "Acquired" (inbound) delegated bandwidth/energy fields: [3](#0-2) 

It does not clear:
- the account's own frozen balances (`FrozenList`, `FrozenV2` for bandwidth/energy) that an attacker could have pre-frozen at that address,
- outbound delegated resources (`DelegatedFrozenBalanceForBandwidth/Energy`, `DelegatedFrozenV2BalanceForBandwidth/Energy`) that the pre-funded address delegated to other accounts,
- the actual `DelegatedResourceStore`/`DelegatedResourceAccountIndexStore` entries tying that address to counterparties,
- the account's `VotesList`/vote-related TronPower state.

The codebase's own test suite demonstrates this exact pre-funding pattern is reachable and expected to be handled: `FreezeTest.testCreate2SuicideToBlackHole`/`testCreate2SuicideToAccount` explicitly compute the CREATE2 address ahead of deployment and call `freezeForSelf`/`freezeForOther` on it before the contract is actually deployed [4](#0-3) , confirming that arbitrary unprivileged users can and do write meaningful account state to a not-yet-deployed contract address, and that only the delegated-resource "acquired" fields are cleared afterward, similar to the incomplete clearing shown above.

### Impact Explanation
An attacker who predicts a `CREATE2` deployment address can:
- Pre-freeze TRX for bandwidth/energy at that address, or pre-vote for witnesses using that address's future TronPower, causing the deployed contract to inherit (or interfere with) resource/vote accounting it never legitimately initiated.
- Pre-delegate resources to or from the predicted address, leaving stale `DelegatedResourceStore` entries and outbound delegated balances associated with a contract address after deployment, corrupting bandwidth/energy accounting between the contract and third parties.
- Cause resource/asset accounting divergence between the deployer's expectations and the actual on-chain state of the freshly created contract, analogous to the withdrawal-credential hijack in the original report where a legitimate large operation inherits attacker-controlled prior state.

### Likelihood Explanation
Any account can freely predict a `CREATE2`/`CREATE` address (deterministic from factory address, sender, salt, and code hash) and send ordinary, unprivileged transactions (freeze, delegate, vote) to that address before the factory actually deploys the contract. No special permissions or validator role are required, and the existing test suite already exercises pre-funding of predicted CREATE2 addresses, showing the scenario is a realistic and anticipated one that the current cleanup logic does not fully cover.

### Recommendation
When converting an existing (non-contract) account into a newly deployed contract account in `createContractImpl`, fully reset all resource/vote-related state rather than only the "acquired" delegated balances: clear `FrozenList`/`FrozenV2` balances, outbound `DelegatedFrozenBalance(V2)For{Bandwidth,Energy}`, associated `DelegatedResourceStore`/`DelegatedResourceAccountIndexStore` entries, and the account's `VotesList`/TronPower fields, mirroring the more complete cleanup already used elsewhere (e.g. `clearOwnerFreezeV2`) before the account is marked as `AccountType.Contract`.

### Proof of Concept
1. Deploy a factory contract exposing a `CREATE2` deployment function and an address-prediction view function (as in `Create2Test`/`FreezeTest.sol`).
2. Before triggering deployment, as an arbitrary unprivileged account, compute the predicted address (`WalletUtil.generateContractAddress2`) and send ordinary transactions to it: freeze TRX for bandwidth/energy, delegate resources to a third-party receiver, and/or vote for a witness using that address.
3. Trigger the factory's deployment function to actually deploy the contract at the predicted address.
4. Inspect the resulting `AccountCapsule` for the deployed contract address: observe that frozen balances, outbound delegated resource balances/indices, and vote list entries set in step 2 persist post-deployment, while only the inbound "acquired" delegated balances were cleared, as shown by `Program.createContractImpl` and `AccountCapsule.clearDelegatedResource`.

### Citations

**File:** chainbase/src/main/java/org/tron/common/utils/WalletUtil.java (L55-59)
```java
  // for `CREATE2`
  public static byte[] generateContractAddress2(byte[] address, byte[] salt, byte[] code) {
    byte[] mergedData = ByteUtil.merge(address, salt, Hash.sha3(code));
    return Hash.sha3omit12(mergedData);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L827-843)
```java
    AccountCapsule existingAccount = getContractState().getAccount(newAddress);
    boolean contractAlreadyExists = existingAccount != null;

    if (VMConfig.allowTvmConstantinople()) {
      contractAlreadyExists =
          contractAlreadyExists && isContractExist(existingAccount, getContractState());
    }
    Repository deposit = getContractState().newRepositoryChild();
    if (VMConfig.allowTvmConstantinople()) {
      if (existingAccount == null) {
        deposit.createAccount(newAddress, "CreatedByContract",
            AccountType.Contract);
      } else if (!contractAlreadyExists) {
        existingAccount.updateAccountType(AccountType.Contract);
        existingAccount.clearDelegatedResource();
        deposit.updateAccount(newAddress, existingAccount);
      }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L1326-1337)
```java
  // just for vm create2 instruction
  public void clearDelegatedResource() {
    Builder builder = account.toBuilder();
    AccountResource newAccountResource = getAccountResource().toBuilder()
        .setAcquiredDelegatedFrozenBalanceForEnergy(0L)
            .setAcquiredDelegatedFrozenV2BalanceForEnergy(0L)
            .build();
    builder.setAccountResource(newAccountResource);
    builder.setAcquiredDelegatedFrozenBalanceForBandwidth(0L)
            .setAcquiredDelegatedFrozenV2BalanceForBandwidth(0L);
    this.account = builder.build();
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeTest.java (L489-517)
```java
  @Test
  public void testCreate2SuicideToBlackHole() throws Exception {
    byte[] factory = deployContract("FactoryContract", FACTORY_CODE);
    byte[] contract = deployContract("TestFreeze", CONTRACT_CODE);
    long frozenBalance = 1_000_000;
    freezeForSelf(contract, frozenBalance, 0);
    freezeForSelf(contract, frozenBalance, 1);
    long salt = 1;
    byte[] predictedAddr = getCreate2Addr(factory, salt);
    freezeForOther(contract, predictedAddr, frozenBalance, 0);
    freezeForOther(contract, predictedAddr, frozenBalance, 1);
    Assert.assertArrayEquals(predictedAddr, deployCreate2Contract(factory, salt));
    setBalance(predictedAddr, 100_000_000);
    freezeForSelf(predictedAddr, frozenBalance, 0);
    freezeForSelf(predictedAddr, frozenBalance, 1);
    freezeForOther(predictedAddr, userA, frozenBalance, 0);
    freezeForOther(predictedAddr, userA, frozenBalance, 1);
    suicideWithException(predictedAddr, predictedAddr);
    clearDelegatedExpireTime(predictedAddr, userA);
    unfreezeForOther(predictedAddr, userA, 0);
    unfreezeForOther(predictedAddr, userA, 1);
    suicideToAccount(predictedAddr, predictedAddr);

    unfreezeForOtherWithException(contract, predictedAddr, 0);
    unfreezeForOtherWithException(contract, predictedAddr, 1);
    clearDelegatedExpireTime(contract, predictedAddr);
    unfreezeForOther(contract, predictedAddr, 0);
    unfreezeForOther(contract, predictedAddr, 1);
  }
```
