### Title
CEA inbound smart-contract path lets any unprivileged source-chain caller force `executeUniversalTx` on an arbitrary Push Chain contract — ([File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go], [File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go])

### Summary
`CallExecuteUniversalTx` fixes a specific, statically-encoded selector (`executeUniversalTx(string,bytes,bytes,uint256,address,bytes32)`) with a hardcoded shape, but the *target address* (`ueaAddr`, i.e. `utx.InboundTx.Recipient`), the `payload` bytes, the `ceaAddress` (`utx.InboundTx.Sender`), and the `amount`/`prc20AssetAddr` are all sourced from a source-chain gateway event that is entirely attacker-controlled. Any unprivileged holder of funds on a supported external chain can call the Push Chain gateway with `isCEA=true`, set `Recipient` to the address of *any deployed EVM contract already on Push Chain* (whether or not that contract ever opted into the CEA integration), and have honest Universal Validators faithfully vote the observation and honest core-validator code execute `DerivedEVMCall(..., isModuleSender=true, "executeUniversalTx", sourceChain, ceaAddress, payload, amount, prc20Addr, txId)` against that contract, from the `uexecutor` module account.

### Finding Description
For `IsCEA=true` inbounds, `ExecuteInboundFundsAndPayload` / `ExecuteInboundGasAndPayload` classify the recipient into three buckets purely from on-chain bytecode inspection: [1](#0-0) 

If the recipient is *not* a UEA (per `CallFactoryGetOriginForUEA`) but *does* have deployed bytecode, the module unconditionally treats it as a legitimate "CEA smart contract" and calls `executeUniversalTx` on it: [2](#0-1) 

The actual EVM call is issued as the `uexecutor` module account (a privileged sender with no ECDSA key, using the synthetic module-signer machinery) via `DerivedEVMCall` with `isModuleSender=true`: [3](#0-2) 

Nothing in this path validates that:
- the target contract at `utx.InboundTx.Recipient` ever registered/opted in as a CEA integration, or
- the `sourceChain` / `ceaAddress` (attacker's own source-chain sender string, verbatim) correspond to any relationship the target contract expects, or
- `payload` (raw attacker-supplied bytes from `utx.InboundTx.UniversalPayload.Data`, itself derived from `raw_payload` decoded by the core validator per README) is anything the target contract was designed to receive.

Because `Recipient`, `Amount`, `AssetAddr`, and `raw_payload`/payload data are all fields of the `Inbound` message that originate from a gateway event on the *source chain* — fully under the control of whoever calls the gateway contract there — an unprivileged external user can pick **any already-deployed contract address on Push Chain** as `Recipient`, and honest UVs will vote and honest core-validator code will call that contract's `executeUniversalTx(sourceChain, ceaAddress, payload, amount, prc20AssetAddr, txId)` function as the trusted `uexecutor` module. Any contract on Push Chain that happens to expose a function matching this selector (`executeUniversalTx(string,bytes,bytes,uint256,address,bytes32)`) — regardless of whether it was ever meant to receive protocol-originated calls — will be invoked with attacker-chosen `payload` bytes, on behalf of the module account. This is unauthorized module-originated EVM execution against a target that never explicitly registered to receive it, driven entirely by unprivileged user input (the source-chain gateway call), matching the "unauthorized module-originated EVM execution" impact category in the Allowed Impact Gate.

This is the direct native analog of the HAL-01/broken access control report: there, an attacker obtained a resource ID (`orgId`) through one flow and used it to hit a GET endpoint that never verified the ID belonged to the caller. Here, an attacker supplies a `Recipient` "ID" (any EVM contract address) through the inbound-vote flow and the module-originated execution path never verifies that the resolved address is a contract that consented to being a CEA execution target — it only checks "has bytecode."

### Impact Explanation
Depending on what code exists at the attacker-chosen `Recipient` address (any deployed contract on Push Chain that happens to define a matching-selector fallback/function), this can:
- trigger unintended state changes in third-party contracts by masquerading as a legitimate protocol-originated call from the trusted `uexecutor` module account,
- allow probing/triggering of contract logic gated only on `msg.sender == module` assumptions that developers may reasonably have made believing only genuinely-integrated CEA contracts would ever be called this way,
- combined with attacker-controlled `amount`/`prc20AssetAddr` (an ERC20/PRC20 token address the attacker also controls via `AssetAddr`/token-config mapping), could misroute deposits or corrupt token/accounting assumptions inside a victim contract that trusts the module as an authenticated caller.

The severity is bounded by what logic exists behind the matching selector on deployed Push Chain contracts, which is why I present this as a "High" (not "Critical") impact, mirroring the report's own downgrade rationale (impact contingent on a secondary condition — here, a contract matching the selector must exist and act on unauthenticated `sourceChain`/`ceaAddress`/`payload` input).

### Likelihood Explanation
Likelihood is High: the entire trigger path is reachable by an ordinary, unprivileged external-chain user with no relayer/validator/admin privileges — they only need to call the source-chain gateway contract with attacker-chosen calldata (`isCEA=true`, arbitrary `Recipient`), which is a default, permissionless user action explicitly supported by the protocol's CEA feature. Honest UVs and honest core-validator code process this exactly as designed; no forged votes or dishonest validators are required.

### Recommendation
Require an explicit opt-in registry for CEA smart-contract recipients (e.g., an allowlist keyed by `(chain, contract address)` in `x/uregistry`, analogous to `TokenConfig`), and have `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` check that `Recipient` is present in this allowlist before invoking `CallExecuteUniversalTx`. Reject (fail the PCTx, as already done for non-contract non-UEA recipients) any CEA inbound whose recipient contract has not registered to receive module-originated `executeUniversalTx` calls.

### Proof of Concept
1. Deploy or identify any contract `V` on Push Chain that exposes a function matching the selector for `executeUniversalTx(string,bytes,bytes,uint256,address,bytes32)` (per `RecipientContractABI` in [4](#0-3) ), without `V` ever having registered as a CEA integration with Push Chain.
2. As an unprivileged user on a supported source chain (e.g. Ethereum Sepolia), call the Push Chain gateway contract to emit a gateway event with `isCEA=true`, `Recipient = address(V)`, and attacker-chosen `raw_payload`/`Amount`/`AssetAddr`.
3. Honest UVs observe and vote `MsgVoteInbound` for this event; upon quorum, the core validator's `ExecuteInboundFundsAndPayload` classifies `V` as a "smart contract" (non-UEA, has bytecode) and calls `k.CallExecuteUniversalTx(cacheCtx, address(V), sourceChain, senderBytes, payload, amount, prc20Addr, txId)` as the `uexecutor` module account, per [5](#0-4) .
4. `V.executeUniversalTx(...)` executes with `msg.sender == uexecutor module` and fully attacker-controlled `sourceChain`, `ceaAddress`, `payload`, `amount`, `prc20AssetAddr` arguments — confirming that no consent/registration check gates which contracts the module will call into.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L59-87)
```go
		if !strings.HasPrefix(strings.ToLower(utx.InboundTx.Recipient), "0x") {
			execErr = fmt.Errorf("recipient must be a valid hex address when isCEA is true")
		} else {
			ueaAddr = common.HexToAddress(utx.InboundTx.Recipient)

			_, isUEA, ueaCheckErr := k.CallFactoryGetOriginForUEA(sdkCtx, ueModuleAccAddress, factoryAddress, ueaAddr)
			if ueaCheckErr != nil {
				execErr = fmt.Errorf("failed to verify UEA: %w", ueaCheckErr)
			} else if isUEA {
				// UEA path: deposit PRC20 into the UEA (if amount > 0), then execute payload via UEA
				if inboundAmount.Sign() > 0 {
					receipt, execErr = k.depositPRC20(
						sdkCtx,
						utx.InboundTx.SourceChain,
						utx.InboundTx.AssetAddr,
						ueaAddr,
						utx.InboundTx.Amount,
					)
					if execErr != nil {
						execErr = fmt.Errorf("depositPRC20 failed: %w", execErr)
					}
				}
			} else {
				// Non-UEA: check if recipient has code (smart contract) vs EOA
				codeHash := k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr)
				if codeHash != types.EmptyCodeHash && codeHash != (common.Hash{}) {
					// Smart contract: will call executeUniversalTx after deposit
					isSmartContract = true
				}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L208-256)
```go
	// Smart contract path: call executeUniversalTx and return
	if isSmartContract {
		tokenConfig, tcErr := k.uregistryKeeper.GetTokenConfig(sdkCtx, utx.InboundTx.SourceChain, utx.InboundTx.AssetAddr)

		var contractReceipt *evmtypes.MsgEthereumTxResponse
		var contractErr error
		var feeErr error

		if tcErr != nil {
			contractErr = fmt.Errorf("token config lookup failed: %w", tcErr)
		} else {
			prc20Addr := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)

			amount := new(big.Int)
			amount, ok := amount.SetString(utx.InboundTx.Amount, 10)
			if !ok {
				contractErr = fmt.Errorf("invalid amount: %s", utx.InboundTx.Amount)
			} else {
				txId := common.HexToHash(utx.Id)

				var payload []byte
				if utx.InboundTx.UniversalPayload != nil && utx.InboundTx.UniversalPayload.Data != "" {
					payload = common.FromHex(utx.InboundTx.UniversalPayload.Data)
				}

				// Wrap the EVM call + fee deduction in a CacheContext so they
				// commit/revert together. If fee deduction fails, the EVM state
				// changes from executeUniversalTx are discarded — closes the
				// free-execution gap when the recipient contract has no native
				// UPC to cover gas. The deposit (above this scope) stays
				// committed regardless.
				cacheCtx, writeCache := sdkCtx.CacheContext()
				contractReceipt, contractErr = k.CallExecuteUniversalTx(
					cacheCtx,
					ueaAddr,
					utx.InboundTx.SourceChain,
					[]byte(utx.InboundTx.Sender),
					payload,
					amount,
					prc20Addr,
					txId,
				)
				if contractErr == nil {
					feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
					if feeErr == nil {
						writeCache()
					}
				}
			}
```

**File:** x/uexecutor/keeper/evm.go (L646-692)
```go
// CallExecuteUniversalTx calls executeUniversalTx on a smart-contract recipient.
// This is used for isCEA inbounds whose recipient is a deployed contract (not a UEA).
func (k Keeper) CallExecuteUniversalTx(
	ctx sdk.Context,
	recipientAddr common.Address,
	sourceChain string,
	ceaAddress []byte,
	payload []byte,
	amount *big.Int,
	prc20AssetAddr common.Address,
	txId [32]byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	recipientABI, err := types.ParseRecipientContractABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse recipient contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		recipientABI,
		ueModuleAccAddress,
		recipientAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"executeUniversalTx",
		sourceChain,
		ceaAddress,
		payload,
		amount,
		prc20AssetAddr,
		txId,
	)
}
```

**File:** x/uexecutor/types/abi.go (L870-886)
```go
// RecipientContractABI is the ABI for smart-contract recipients that implement executeUniversalTx.
const RecipientContractABI = `[
  {
    "type": "function",
    "name": "executeUniversalTx",
    "inputs": [
      { "name": "sourceChain",    "type": "string"  },
      { "name": "ceaAddress",     "type": "bytes"   },
      { "name": "payload",        "type": "bytes"   },
      { "name": "amount",         "type": "uint256" },
      { "name": "prc20AssetAddr", "type": "address" },
      { "name": "txId",           "type": "bytes32" }
    ],
    "outputs": [],
    "stateMutability": "nonpayable"
  }
]`
```
