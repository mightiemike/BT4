### Title
`getCanWithdrawUnfreezeAmount` ignores the `supportUnfreezeDelay` governance switch and reports withdrawable balance that cannot actually be withdrawn - (File: `framework/src/main/java/org/tron/core/Wallet.java`)

### Summary
The read-only query `Wallet.getCanWithdrawUnfreezeAmount` computes the amount a user could withdraw from their `unfrozenV2` list purely based on expiry timestamps, without checking whether the `WithdrawExpireUnfreeze`/`UnfreezeBalanceV2` feature is currently enabled by the committee (`supportUnfreezeDelay`). This mirrors the vMaia `maxWithdraw`/`maxRedeem` bug, where a view function meant to report the maximum withdrawable amount fails to return `0` when withdrawals are globally paused, instead returning a nonzero value that the actual withdraw call will revert on.

### Finding Description
`WithdrawExpireUnfreezeActuator.validate()` and `UnfreezeBalanceV2Actuator.validate()` both gate the real state-changing operations behind the dynamic property flag `supportUnfreezeDelay`, reverting with `"Not support WithdrawExpireUnfreeze transaction, need to be opened by the committee"` if the feature is disabled by committee governance: [1](#0-0) [2](#0-1) 

However, the corresponding "max withdrawable" read-only API, `Wallet.getCanWithdrawUnfreezeAmount`, only filters the account's `unfrozenV2` entries by expiry time — it never checks `dynamicStore.supportUnfreezeDelay()`: [3](#0-2) 

This function is exposed over both HTTP (`GetCanWithdrawUnfreezeAmountServlet`) and gRPC (`RpcApiService.getCanWithdrawUnfreezeAmount`), making it the public "how much can I withdraw" endpoint analogous to ERC-4626's `maxWithdraw`/`maxRedeem`: [4](#0-3) [5](#0-4) 

By contrast, the TVM-facing precompile equivalent (`ExpireUnfreezeBalanceV2` / `FreezeV2Util.queryExpireUnfreezeBalanceV2`) at least gates on `VMConfig.allowTvmFreezeV2()` before reporting a nonzero value, showing that the codebase's own convention for these "queryable max" functions is to reflect the feature's enabled/disabled state: [6](#0-5) 

`getCanWithdrawUnfreezeAmount` breaks that convention: if the committee toggles `supportUnfreezeDelay` off after users have already accumulated expired `unfrozenV2` entries (entries created while the feature was previously enabled), the query keeps reporting a positive withdrawable amount, but any actual `WithdrawExpireUnfreezeContract` transaction submitted based on that value will revert.

### Impact Explanation
This is a state/accounting divergence between the public "preview" API and the actual on-chain executable behavior — directly analogous to the ERC-4626 `maxWithdraw`/`maxRedeem` spec violation. Wallets, exchanges, and other integrators rely on `getCanWithdrawUnfreezeAmount` to decide whether to submit a withdraw transaction or to display balances to end users. When the feature is administratively paused, this endpoint misleadingly reports available funds, causing failed transactions (wasted fees/bandwidth) and potential accounting errors in downstream systems (e.g., exchanges crediting/displaying balances that cannot be withdrawn). It does not directly lead to fund loss or consensus divergence, matching the same severity class (informational/low, EIP/interface-compliance style bug) as the original finding.

### Likelihood Explanation
Likelihood depends on the committee actually disabling `supportUnfreezeDelay` after it has been enabled and users have accrued `unfrozenV2` entries — a plausible governance action but not something an ordinary user can trigger on demand. The divergence is deterministic and easily reproducible whenever that governance state occurs, but requires this specific configuration change to manifest.

### Recommendation
Update `Wallet.getCanWithdrawUnfreezeAmount` to check `dynamicStore.supportUnfreezeDelay()` (mirroring the check already used in `WithdrawExpireUnfreezeActuator.validate()`) and return `0` (or an explicit "unavailable" response) when the feature is disabled, rather than computing a value from expired `unfrozenV2` entries that cannot actually be withdrawn.

### Proof of Concept
1. Committee enables `supportUnfreezeDelay`; a user calls `UnfreezeBalanceV2Contract`, creating an `UnFreezeV2` entry with a future `unfreezeExpireTime`.
2. Time passes and the entry's `unfreezeExpireTime` is now `<= now`, i.e., the entry is "expired" and normally withdrawable.
3. Committee disables `supportUnfreezeDelay` via a proposal.
4. Client calls `GET /wallet/getcanwithdrawunfreezeamount` (or the gRPC equivalent) — `Wallet.getCanWithdrawUnfreezeAmount` still sums and returns the expired entry's amount as withdrawable, since it never checks `supportUnfreezeDelay`. [7](#0-6) 
5. Client submits `WithdrawExpireUnfreezeContract` based on that reported amount; `WithdrawExpireUnfreezeActuator.validate()` reverts with `"Not support WithdrawExpireUnfreeze transaction, need to be opened by the committee"`, confirming the query result was inconsistent with actual executable state. [1](#0-0)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java (L84-87)
```java
    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support WithdrawExpireUnfreeze transaction,"
          + " need to be opened by the committee");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L119-122)
```java
    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support UnfreezeV2 transaction,"
          + " need to be opened by the committee");
    }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L922-951)
```java
  public GrpcAPI.CanWithdrawUnfreezeAmountResponseMessage getCanWithdrawUnfreezeAmount(
          ByteString ownerAddress, long timestamp) {
    GrpcAPI.CanWithdrawUnfreezeAmountResponseMessage.Builder builder =
            GrpcAPI.CanWithdrawUnfreezeAmountResponseMessage.newBuilder();
    if (timestamp < 0) {
      return builder.build();
    }

    long canWithdrawUnfreezeAmount;

    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    AccountCapsule accountCapsule = accountStore.get(ownerAddress.toByteArray());
    if (accountCapsule == null) {
      return builder.build();
    }

    if (timestamp == 0) {
      timestamp = dynamicStore.getLatestBlockHeaderTimestamp();
    }

    List<UnFreezeV2> unfrozenV2List = accountCapsule.getInstance().getUnfrozenV2List();
    long finalTimestamp = timestamp;

    canWithdrawUnfreezeAmount = unfrozenV2List
            .stream().filter(unfrozenV2 -> unfrozenV2.getUnfreezeExpireTime() <= finalTimestamp)
            .mapToLong(UnFreezeV2::getUnfreezeAmount).sum();

    builder.setAmount(canWithdrawUnfreezeAmount);
    return builder.build();
```

**File:** framework/src/main/java/org/tron/core/services/http/GetCanWithdrawUnfreezeAmountServlet.java (L59-70)
```java
  private void fillResponse(boolean visible,
                            ByteString ownerAddress,
                            long timestamp,
                            HttpServletResponse response) throws IOException {
    GrpcAPI.CanWithdrawUnfreezeAmountResponseMessage reply =
            wallet.getCanWithdrawUnfreezeAmount(ownerAddress, timestamp);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L576-588)
```java
    @Override
    public void getCanWithdrawUnfreezeAmount(CanWithdrawUnfreezeAmountRequestMessage request,
        StreamObserver<GrpcAPI.CanWithdrawUnfreezeAmountResponseMessage> responseObserver) {
      try {
        responseObserver
                .onNext(wallet.getCanWithdrawUnfreezeAmount(
                        request.getOwnerAddress(), request.getTimestamp())
        );
      } catch (Exception e) {
        responseObserver.onError(getRunTimeException(e));
      }
      responseObserver.onCompleted();
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L23-37)
```java
  public static long queryExpireUnfreezeBalanceV2(byte[] address, long time, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return 0;
    }

    AccountCapsule accountCapsule = repository.getAccount(address);
    if (accountCapsule == null) {
      return 0;
    }

    List<Protocol.Account.UnFreezeV2> unfrozenV2List =
        accountCapsule.getInstance().getUnfrozenV2List();

    return getTotalWithdrawUnfreeze(unfrozenV2List, time);
  }
```
