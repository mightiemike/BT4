Based on my research, I found a concrete analog to the 31Third "user can pass any calldata" bug class within the isCEA (Chain-Enabled Abstraction) inbound path.

### Title
CEA recipient contracts receive attacker-controlled `payload`, `sourceChain`, and `ceaAddress` bytes decoupled from any signature or binding to the actual deposit - (File: `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/evm.go`)

### Summary
For `isCEA=true` inbounds where the `Recipient` is a plain deployed smart contract (not a UEA), the module calls `executeUniversalTx(sourceChain, ceaAddress, payload, amount, prc20AssetAddr, txId)` on that recipient <cite repo="Oyahkilomeikhide/push-chain-node--018" path="x/uexecutor/keeper/evm.go" start="646="691" /> as the `uexecutor` module account, using `sourceChain`, `ceaAddress` (= `utx.InboundTx.Sender`), and `payload` (= `utx.InboundTx.UniversalPayload.Data`) taken directly from the observed `Inbound` fields [1](#0-0) . Unlike the UEA path, there is no `verificationData`/signature check binding this `payload` to anything — it is raw bytes copied out of the cross-chain event as decoded by Universal Validators (honest, per scope), with no cryptographic tie between `payload` and `amount`/`prc20AssetAddr`. This mirrors 31Third's core flaw: a struct (here, `amount` + `prc20AssetAddr`, analogous to 31Third's declared trade token/amount fields) is passed alongside separately-sourced, unconstrained `payload` bytes (analogous to 31Third's raw calldata) with no requirement that the two agree.

### Finding Description
`CallExecuteUniversalTx` builds a `DerivedEVMCall` to the recipient contract's `executeUniversalTx(string,bytes,bytes,uint256,address,bytes32)` [2](#0-1) . The `payload` argument is `common.FromHex(utx.InboundTx.UniversalPayload.Data)` — attacker-controlled bytes originally supplied by whoever emitted the source-chain event (a normal, unprivileged user on the source chain) — while `amount`/`prc20AssetAddr` are derived independently from the inbound's `Amount`/`AssetAddr` fields and the token registry [3](#0-2) . Push-chain-node itself never validates that `payload` encodes anything consistent with `amount`/`prc20AssetAddr`, nor that `ceaAddress` (raw `Sender` bytes) is meaningfully bound to `payload`. The node's only authorization gate on this call is that the call originates from the `uexecutor` module account (`isModuleSender=true`) [4](#0-3)  — it forwards whatever bytes the attacker put in the source-chain payload field verbatim, with no on-chain (Go-side) semantic check that they match the declared `amount`/token.

### Impact Explanation
Whether this is exploitable for fund drain depends entirely on the recipient CEA contract's own trust assumptions (which live in externally-deployed Solidity, out of this repo's scope) — if a CEA contract naively executes `payload` as generic calldata to a sub-target rather than treating it as opaque application data, the mismatch between attacker-supplied `payload` and the declared `amount`/`prc20AssetAddr` could let an attacker cause the contract to act on tokens/values it didn't actually receive, directly paralleling BatchTrade's mismatched-calldata drain. Within push-chain-node's own scope, however, no funds move based on `payload` content — `depositPRC20`/`gasAndPayloadDepositAutoSwap` mint exactly `amount` of `prc20AssetAddr` before the call [3](#0-2) , and `payload` is only forwarded, not interpreted, by the Go keeper.

### Likelihood Explanation
Reachable by any unprivileged external-chain user simply by emitting a gateway event with `isCEA=true`, an arbitrary `Recipient` contract address, and arbitrary `UniversalPayload.Data` — no validator collusion or privileged access required; honest UVs will faithfully relay whatever the source-chain sender emitted, since `Inbound.UniversalPayload.Data` is defined as attacker intent by design, not attacker-authenticated data.

### Recommendation
Because the root cause of exploitability lies in what the CEA recipient contract does with `payload` (outside this repo), push-chain-node cannot fully close this by itself. Nonetheless, harden the boundary: document and/or enforce that `payload` passed to `executeUniversalTx` is guaranteed to be opaque, untrusted, unauthenticated data from the source chain (already partly documented at [5](#0-4) ), and consider adding an explicit warning/allowlist mechanism at the registry level (`x/uregistry`) for which CEA recipient addresses are permitted to receive `isCEA=true` inbounds, since currently any deployed contract address supplied as `Recipient` is accepted without any registry-side vetting [6](#0-5) .

### Proof of Concept
1. Attacker deploys a CEA contract on Push Chain whose `executeUniversalTx` naively forwards `payload` as calldata to some sub-call (e.g., a DEX router) using the deposited `prc20AssetAddr`/`amount` as msg.sender's balance reference, but does not verify `payload` actually references the same token/amount.
2. Attacker submits a source-chain gateway transaction with `TxType=GAS_AND_PAYLOAD`, `isCEA=true`, `Recipient=<attacker's CEA contract>`, `Amount`/`AssetAddr` set to a low-value or victim token, and `UniversalPayload.Data` crafted to reference a different, higher-value token/amount recognized by the CEA contract's internal logic.
3. Honest UVs observe and vote the inbound faithfully (per scope, no collusion needed) since `UniversalPayload.Data` is defined as pass-through user intent.
4. `ExecuteInboundGasAndPayload` mints `amount` of `prc20AssetAddr` to the CEA contract, then calls `executeUniversalTx(sourceChain, ceaAddress, payload, amount, prc20AssetAddr, txId)` [7](#0-6)  with the crafted `payload`.
5. If the CEA contract's logic trusts `payload` for value-moving decisions independent of the passed `amount`/`prc20AssetAddr`, this allows the attacker to redirect or drain other funds already held by the CEA contract — analogous to BatchTrade's declared-struct vs. actual-calldata mismatch.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L67-89)
```go
				if !strings.HasPrefix(strings.ToLower(utx.InboundTx.Recipient), "0x") {
					execErr = fmt.Errorf("recipient must be a valid hex address when isCEA is true")
				} else {
					ueaAddr = common.HexToAddress(utx.InboundTx.Recipient)

					_, isUEA, ueaCheckErr := k.CallFactoryGetOriginForUEA(sdkCtx, ueModuleAccAddress, factoryAddress, ueaAddr)
					if ueaCheckErr != nil {
						execErr = fmt.Errorf("failed to verify UEA: %w", ueaCheckErr)
					} else if isUEA {
						// UEA path: deposit + autoswap into the UEA (if amount > 0), then execute payload via UEA
						if amount.Sign() > 0 {
							prc20AddrHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
							receipt, execErr = k.gasAndPayloadDepositAutoSwap(sdkCtx, prc20AddrHex, ueaAddr, amount)
							if execErr != nil {
								execErr = fmt.Errorf("depositAutoSwap failed: %w", execErr)
							}
						}
					} else {
						// Non-UEA: check if recipient has code (smart contract) vs EOA
						codeHash := k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr)
						if codeHash != types.EmptyCodeHash && codeHash != (common.Hash{}) {
							isSmartContract = true
						}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L216-248)
```go
	// Smart contract path (isCEA): call executeUniversalTx and return
	if isSmartContract {
		prc20Addr := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)

		scAmount := new(big.Int)
		scAmount, ok := scAmount.SetString(utx.InboundTx.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", utx.InboundTx.Amount)
		}

		txId := common.HexToHash(utx.Id)

		var payload []byte
		if utx.InboundTx.UniversalPayload != nil && utx.InboundTx.UniversalPayload.Data != "" {
			payload = common.FromHex(utx.InboundTx.UniversalPayload.Data)
		}

		// Wrap the EVM call + fee deduction in a CacheContext so they
		// commit/revert together. If fee deduction fails, the EVM state
		// changes from executeUniversalTx are discarded — closes the
		// free-execution gap when the recipient contract has no native
		// UPC to cover gas.
		cacheCtx, writeCache := sdkCtx.CacheContext()
		contractReceipt, contractErr := k.CallExecuteUniversalTx(
			cacheCtx,
			ueaAddr,
			utx.InboundTx.SourceChain,
			[]byte(utx.InboundTx.Sender),
			payload,
			scAmount,
			prc20Addr,
			txId,
		)
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

**File:** x/uexecutor/keeper/evm.go (L673-691)
```go
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
```

**File:** x/uexecutor/README.md (L43-61)
```markdown
#### 1. `Inbound` — the source-chain observation

Filled in once, when the inbound vote is finalized. After that, it is read-only.

```protobuf
message Inbound {
  string source_chain        = 1;  // CAIP-2, e.g. "eip155:11155111"
  string tx_hash             = 2;  // unique source-chain tx hash
  string sender              = 3;  // source-chain sender address
  string recipient           = 4;  // destination address on Push Chain (UEA or contract)
  string amount              = 5;  // bridged amount (synthetic token, uint256 as string)
  string asset_addr          = 6;  // source-chain ERC20 / native token address
  string log_index           = 7;  // log index that emitted this inbound (uniqueness within tx)
  TxType tx_type             = 8;  // see TxType table below
  UniversalPayload universal_payload = 9;  // the user's intent (decoded from raw_payload)
  string verification_data   = 10; // bytes the UEA uses to authenticate the payload
  RevertInstructions revert_instructions = 11;  // where funds go on revert
  bool   isCEA               = 12; // recipient is a contract (CEA) instead of a UEA
  string raw_payload         = 13; // hex-encoded raw event bytes (decoded by core validator)
```
