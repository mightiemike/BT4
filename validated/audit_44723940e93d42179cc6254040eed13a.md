### Title
Unconditional transfer of both `call_value` (TRX) and `call_token_value` (TRC10) in `TriggerSmartContract` leads to permanent loss of user funds when the invoked function does not consume the mismatched currency - (File: `actuator/src/main/java/org/tron/core/actuator/VMActuator.java`)

### Summary
`TriggerSmartContract` lets a caller independently set `call_value` (TRX) and `call_token_value` (TRC10, via `token_id`) in the same request. `VMActuator.call()` unconditionally moves both amounts from the caller to the target contract before/around bytecode execution, with no validation that the invoked contract function actually expects or handles both types of value simultaneously. This mirrors the Infinity Exchange bug, where `msg.value` was blindly accepted even for calls whose `currency` was an ERC20 token, permanently freezing the mistakenly-sent ETH.

### Finding Description
In `VMActuator.call()`, `callValue` and `tokenValue` are read straight from the `TriggerSmartContract` message and unconditionally transferred to the contract address: [1](#0-0) 

The values originate directly from user-controlled fields on the transaction, with no coupling to what the contract's ABI/function selector actually needs: [2](#0-1) 

For contract creation the same unconditional dual transfer pattern exists: [3](#0-2) 

Solidity's compiler enforces `payable`/non-payable semantics only for the native TRX value (`msg.value`), because that's the only currency the EVM/TVM ABI natively models. `trcToken` transfers are a TRON-specific extension (`transferToken`, `msg.tokenvalue`, etc.), and ordinary non-token-aware contracts have no bytecode-level guard against receiving an unsolicited TRC10 payment via `call_token_value`. Because `checkTokenValueAndId()` only validates that `tokenId`/`tokenValue` are internally consistent (not zero/negative mismatches) and never checks whether the call is actually meant to be a token-bearing call, any TRC10 amount attached to a call to a plain (non-token-aware) function is silently absorbed into the contract's balance: [4](#0-3) 

This is the same root cause class as the Infinity Exchange issue: a currency parameter (`msg.value` there, `call_token_value` here) is accepted and moved into contract custody purely based on "is it non-zero", without confirming the call path is actually designed to consume that currency.

### Impact Explanation
A user (or a wallet/dApp integration bug) that sets `call_token_value`/`token_id` on a `TriggerSmartContract` call targeting a function/contract that has no logic to return or account for TRC10 tokens will have that TRC10 balance permanently locked in the target contract, unless the contract happens to expose some sweep/withdraw mechanism for arbitrary tokens (most ordinary contracts do not). This is a direct, irrecoverable loss of TRC10 token funds for the caller, reachable by any account issuing a standard `TriggerSmartContract` transaction through the public JSON-RPC (`eth_sendTransaction`-style flows via `TronJsonRpcImpl`), HTTP API (`/wallet/triggersmartcontract`), or gRPC.

### Likelihood Explanation
Likelihood is judged Medium: it requires a user/integration mistake (setting `call_token_value` for a call to a contract/function that isn't designed to receive TRC10), but this is realistic because:
- `TriggerSmartContract`/its HTTP and JSON-RPC builders expose `call_token_value` and `token_id` as generic, independently-settable fields alongside `call_value`, with no cross-validation: [5](#0-4) 
- The same function/opcode path (`call()`) is used for both plain TRX calls and TRC10-bearing calls, exactly the pattern flagged as "high enough probability" in the original Infinity finding because "the same functions are used for both ETH and ERC20 orders."

### Recommendation
Add a validation step in `VMActuator.call()`/`create()` (or upstream in `checkTokenValueAndId`) that rejects transactions where `tokenValue > 0` but the repository/registry has no record that the target contract is token-aware (e.g., requires an explicit opt-in flag on the contract, or requires the call data to match a known token-receiving selector). At minimum, mirror the recommended C4 fix's spirit: don't allow a currency amount to be silently accepted when the call semantics give no indication the receiving code path consumes it — e.g., surface a clear validate-time error such as "call_token_value set but target is not a valid token receiver" rather than silently transferring and leaving funds stranded.

### Proof of Concept
1. Deploy a plain, non-token-aware TRC20/utility contract (no `transferToken`/token accounting logic).
2. Send a `TriggerSmartContract` transaction to that contract with `call_value = 0`, `call_token_value = N` (N > 0), and `token_id` set to some TRC10 asset, targeting an arbitrary existing function selector.
3. `VMActuator.call()` executes: `tokenValue = contract.getCallTokenValue()` is read, `checkTokenValueAndId` passes (nonzero value with nonzero id is valid), and after VM execution, `MUtil.transferToken(rootRepository, callerAddress, contractAddress, String.valueOf(tokenId), tokenValue)` unconditionally moves the TRC10 tokens to the contract regardless of whether the invoked function used or accounted for them. [6](#0-5) [7](#0-6) 
4. The contract has no mechanism to return the TRC10 tokens, so the caller's tokens are permanently stuck in the contract's balance.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L487-502)
```java
    long callValue = contract.getCallValue();
    long tokenValue = 0;
    long tokenId = 0;
    if (VMConfig.allowTvmTransferTrc10()) {
      tokenValue = contract.getCallTokenValue();
      tokenId = contract.getTokenId();
    }

    if (StorageUtils.getEnergyLimitHardFork()) {
      if (callValue < 0) {
        throw new ContractValidateException("callValue must be >= 0");
      }
      if (tokenValue < 0) {
        throw new ContractValidateException("tokenValue must be >= 0");
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L552-560)
```java
    //transfer from callerAddress to targetAddress according to callValue

    if (callValue > 0) {
      MUtil.transfer(rootRepository, callerAddress, contractAddress, callValue);
    }
    if (VMConfig.allowTvmTransferTrc10() && tokenValue > 0) {
      MUtil.transferToken(rootRepository, callerAddress, contractAddress, String.valueOf(tokenId),
          tokenValue);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L662-676)
```java
  public void checkTokenValueAndId(long tokenValue, long tokenId) throws ContractValidateException {
    if (VMConfig.allowTvmTransferTrc10() && VMConfig.allowMultiSign()) {
      // tokenid can only be 0
      // or (MIN_TOKEN_ID, Long.Max]
      if (tokenId <= VMConstant.MIN_TOKEN_ID && tokenId != 0) {
        throw new ContractValidateException("tokenId must be > " + VMConstant.MIN_TOKEN_ID);
      }
      // tokenid can only be 0 when tokenvalue = 0,
      // or (MIN_TOKEN_ID, Long.Max]
      if (tokenValue > 0 && tokenId == 0) {
        throw new ContractValidateException("invalid arguments with tokenValue = "
            + tokenValue + ", tokenId = " + tokenId);
      }
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/TriggerSmartContractServlet.java (L70-72)
```java
      build.setCallTokenValue(Util.getJsonLongValue(jsonObject, "call_token_value"));
      build.setTokenId(Util.getJsonLongValue(jsonObject, "token_id"));
      build.setCallValue(Util.getJsonLongValue(jsonObject, "call_value"));
```
