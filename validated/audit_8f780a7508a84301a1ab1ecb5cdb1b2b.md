### Title
Active-Permission Scope Enforced Only by `ContractType` Bitmap, Not by Target Contract or Calldata Parameters, Allowing a Restricted Sub-Key to Drain All TRC20 Holdings via `TriggerSmartContract`/`approve` - (File: `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java`, `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java`, `actuator/src/main/java/org/tron/core/utils/TransactionUtil.java`)

### Summary
Java-tron's multi-sig "Active permission" feature is meant to let an account owner grant a sub-key ("session"-like restricted key) the ability to perform only a limited set of operations. The scope check, `checkPermissionOperations`, validates only the **transaction's `ContractType`** against a 32-byte operations bitmap — it never inspects the parameters *inside* the contract, such as the target smart-contract address or the ABI-encoded call data of a `TriggerSmartContract`. This mirrors the root cause of the reported `CredibleAccountModule` bug: the validator authorizes an action by its outer selector/type only, ignoring the semantically decisive inner parameter (the `approve()` spender). An account owner who grants a sub-key permission to submit `TriggerSmartContract` transactions (e.g., intending to let it interact with one specific dApp) is unable to constrain *which* contract or *which* function/parameters that key invokes — the key can call `approve()` on any TRC20 token held by the owning account and set itself as spender, then drain the tokens via `transferFrom()`.

### Finding Description
`checkPermissionOperations` is the sole scope-enforcement gate for an Active permission and is invoked from every code path that authorizes a keyed sub-permission transaction (`addSign`, `validateSignature`, `getTransactionApprovedList`, `getTransactionSignWeight`): [1](#0-0) [2](#0-1) [3](#0-2) 

The check only tests `contract.getTypeValue()` against a per-permission 32-byte bitmap; it never unpacks the `Any` payload to inspect the target contract address or calldata. This is used both when accumulating signatures/weight (`getTransactionSignWeight`, `getTransactionApprovedList` in `Wallet.java`) and when finally validating the signed transaction (`TransactionCapsule.checkPermission`/`validateSignature`): [4](#0-3) [5](#0-4) 

Because `ContractType.TriggerSmartContract` is a single coarse-grained bit in the operations bitmap, once an owner enables it for an Active permission (a common, expected configuration to let a sub-key use one particular dApp), that key can build a `TriggerSmartContract` transaction against *any* contract address with *any* ABI-encoded call data — including `approve(spender, amount)` on any TRC20 the SCW/account holds, with the sub-key's own address as spender and the full balance as amount. This is executed by `VMActuator`/the TVM the same as any other `TriggerSmartContract` call; no additional target-contract or parameter-level restriction exists anywhere in the permission-checking pipeline.

This exactly parallels the reported class of bug: `_validateSingleCall`/`_validateBatchCall` authorize by outer selector (`approve` allowed unconditionally) without validating the semantically important inner parameter (the spender). In java-tron, `checkPermissionOperations` authorizes by outer `ContractType` (`TriggerSmartContract` allowed) without validating the semantically important inner parameters (target contract, function selector, spender/amount).

### Impact Explanation
Any account owner who configures an Active permission intending to give a sub-key narrow, single-purpose smart-contract access (a common multisig/hot-key pattern) unintentionally grants that key the ability to call `approve()` (or any other state-changing function) on every TRC20 contract, then have the compromised/hot key itself (or a collaborating address) call `transferFrom` to drain all TRC20 balances of the account — not limited to any specific token, dApp, or amount the owner intended to expose. This is a direct asset-drain/unauthorized-account-operation vector reachable purely through the standard `AccountPermissionUpdateContract` + `TriggerSmartContract` broadcast flow, with no P2P, leaked-key, or privileged-node component beyond the normal multisig delegation feature itself.

### Likelihood Explanation
Any account that has ever granted an Active permission including the `TriggerSmartContract` operation bit to a lower-trust key (a routine multisig configuration, e.g., for automated bots or dApp-specific hot wallets) is exposed. No special network conditions, timing, or additional privilege escalation is required — the sub-key simply broadcasts a normally-signed `TriggerSmartContract` transaction calling `approve()` on the token(s) it wants to steal.

### Recommendation
Do not treat `TriggerSmartContract` as an atomic, all-or-nothing operation bit in the Active permission model. At minimum, extend `checkPermissionOperations` (and the parallel logic in `TransactionCapsule`, `WalletUtil`, `TransactionUtil`) to optionally scope `TriggerSmartContract` permissions to a whitelist of target contract addresses (and ideally function selectors) associated with the permission, similar to how the reported fix requires validating the `approve()` spender parameter rather than just the selector. Document clearly (and warn via API/CLI) that enabling the `TriggerSmartContract` bit for a sub-key permission grants it unrestricted call ability over all contracts, including any token `approve` calls, so owners are not misled into believing scope is finer-grained than it is.

### Proof of Concept
1. Owner account `A` holds USDT, WETH, TRX and configures an `AccountPermissionUpdateContract` giving Active permission `P` (threshold 1) to key `K`, enabling only the `TriggerSmartContract` bit in `P.operations` (intending `K` to interact with a specific dApp).
2. `K` signs and broadcasts a `TriggerSmartContract` transaction with `permission_id` set to `P`'s id, targeting the USDT TRC20 contract, calldata = `approve(K_address, type(uint256).max)`.
3. `Wallet.broadcastTransaction` → `TransactionCapsule.validateSignature` → `checkPermission` calls `checkPermissionOperations(P, contract)`, which passes because only `ContractType.TriggerSmartContract` (not target address/calldata) is checked: [2](#0-1) 
4. Transaction executes via the TVM, `A`'s USDT approval for `K` is now unlimited.
5. `K` calls `transferFrom(A, K, balance)` on the USDT contract in a subsequent unrestricted transaction (or directly, since `transferFrom` is a plain contract call not gated by `P`), draining all of `A`'s USDT — despite the owner's intent to scope `K`'s access narrowly.

### Citations

**File:** chainbase/src/main/java/org/tron/common/utils/WalletUtil.java (L27-37)
```java
  public static boolean checkPermissionOperations(Permission permission, Contract contract)
      throws PermissionException {
    ByteString operations = permission.getOperations();
    if (operations.size() != 32) {
      throw new PermissionException(String.format("operations size must 32, actual: %d",
          operations.size()));
    }
    int contractType = contract.getTypeValue();
    boolean b = (operations.byteAt(contractType / 8) & (1 << (contractType % 8))) != 0;
    return b;
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L468-491)
```java
  public static boolean validateSignature(Transaction transaction,
      byte[] hash, AccountStore accountStore, DynamicPropertiesStore dynamicPropertiesStore)
      throws PermissionException, SignatureException, SignatureFormatException {
    Transaction.Contract contract = transaction.getRawData().getContractList().get(0);
    int permissionId = contract.getPermissionId();
    byte[] owner = getOwner(contract);
    AccountCapsule account = accountStore.get(owner);
    Permission permission = null;
    if (account == null) {
      if (permissionId == 0) {
        permission = AccountCapsule.getDefaultPermission(ByteString.copyFrom(owner));
      }
      if (permissionId == 2) {
        permission = AccountCapsule
            .createDefaultActivePermission(ByteString.copyFrom(owner), dynamicPropertiesStore);
      }
    } else {
      permission = account.getPermissionById(permissionId);
    }
    if (permission == null) {
      throw new PermissionException("permission isn't exit");
    }
    checkPermission(permissionId, permission, contract);
    long weight = checkWeight(permission, transaction.getSignatureList(), hash, null);
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L635-645)
```java
  private static void checkPermission(int permissionId, Permission permission, Transaction.Contract contract) throws PermissionException {
    if (permissionId != 0) {
      if (permission.getType() != PermissionType.Active) {
        throw new PermissionException("Permission type is error");
      }
      //check operations
      if (!checkPermissionOperations(permission, contract)) {
        throw new PermissionException("Permission denied");
      }
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L171-180)
```java
  public static boolean checkPermissionOperations(Permission permission, Contract contract)
      throws PermissionException {
    ByteString operations = permission.getOperations();
    if (operations.size() != 32) {
      throw new PermissionException("operations size must be 32");
    }
    int contractType = contract.getTypeValue();
    boolean b = (operations.byteAt(contractType / 8) & (1 << (contractType % 8))) != 0;
    return b;
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L665-678)
```java
        int permissionId = contract.getPermissionId();
        Permission permission = account.getPermissionById(permissionId);
        if (permission == null) {
          throw new PermissionException("Permission for this, does not exist!");
        }
        if (permissionId != 0) {
          if (permission.getType() != PermissionType.Active) {
            throw new PermissionException("Permission type is wrong!");
          }
          //check operations
          if (!WalletUtil.checkPermissionOperations(permission, contract)) {
            throw new PermissionException("Permission denied!");
          }
        }
```
