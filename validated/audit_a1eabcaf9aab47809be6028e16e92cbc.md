The Lens Protocol bug pattern here is: a governance pause check (`whenNotPaused`) exists only in the top-level entry-point (`LensHub.unfollow()`), while the underlying primitive contract (`FollowNFT`) that performs the same effect is directly reachable and has no equivalent check, letting users bypass the pause. Push Chain has a structurally identical gap around the `IsInboundEnabled` chain-pause flag and the UEA contract.

### Title
Chain-level `IsInboundEnabled` pause is bypassable by calling the UEA contract's `executeUniversalTx` directly via a raw EVM transaction - (File: x/uexecutor/keeper/msg_execute_payload.go)

### Summary
`x/uexecutor`'s `MsgExecutePayload` Cosmos message handler enforces a chain-level pause (`chainConfig.Enabled.IsInboundEnabled`) before allowing a Universal Payload to be executed through a user's UEA. However, the actual authorization and execution logic lives entirely inside the UEA smart contract's `executeUniversalTx`, which only validates the owner's signature/nonce/deadline — it has no knowledge of, or dependency on, the Cosmos-side `IsInboundEnabled` flag. Because the UEA is a normal deployed EVM contract reachable through Push Chain's exposed EVM JSON-RPC endpoint, any account can submit a standard `MsgEthereumTx` directly to the UEA address with a valid pre-signed payload/`verificationData`, completely bypassing `x/uexecutor`'s msg-server pause check.

### Finding Description
`ExecutePayload` in [1](#0-0)  is the only place this invariant is enforced. The actual on-chain call is `CallUEAExecutePayload`, which issues a `DerivedEVMCall` to the UEA contract's `executeUniversalTx` method: [2](#0-1) .

The module's own README documents that authorization is delegated entirely to the UEA contract's signature check, and explicitly notes the contract does not restrict which EVM address (`msg.sender`) may call it: [3](#0-2) .

Since the UEA is a first-class deployed EVM contract, and Push Chain exposes a standard EVM JSON-RPC endpoint (port 8545) as documented in [4](#0-3) , nothing prevents a holder of a valid pre-signed `UniversalPayload` + `verificationData` from submitting a plain `eth_sendRawTransaction` directly to the UEA's `executeUniversalTx`, instead of routing through `MsgExecutePayload`. That path never touches `x/uexecutor`'s msg server, so the `IsInboundEnabled` check is never evaluated — mirroring exactly how `FollowNFT.removeFollower()`/`burn()` never touch `LensHub.unfollow()`'s `whenNotPaused` modifier.

The same asymmetry pattern likely also applies to `MsgMigrateUEA` (`x/uexecutor/keeper/msg_migrate_uea.go`), which similarly gates on `IsInboundEnabled` at the Cosmos layer while the UEA's `migrateUEA` entry point performs its own independent signature check as shown in the ABI: [5](#0-4) .

### Impact Explanation
Governance disabling inbound execution for a chain (`IsInboundEnabled = false`) is meant to be a hard circuit-breaker for `MsgExecutePayload`/UEA execution on accounts derived from that chain — e.g., in response to a discovered bug in payload/verification handling, a compromised signature scheme, or an active exploit. Because the actual state-mutating call path (the UEA contract) enforces no equivalent check, an attacker (or any user holding a valid pre-authorized payload) can continue executing arbitrary universal payloads and moving/spending UEA-held funds through direct EVM transactions even while the chain is supposedly paused. This breaks the intended protocol-wide halt semantics and can be used to continue operating exactly the functionality governance intended to shut off, undermining incident response.

### Likelihood Explanation
This requires no privileged access — any unprivileged external EVM account with a valid pre-signed payload can submit a standard transaction to the known/derivable UEA address via the public EVM RPC. No validator or node cooperation beyond normal transaction inclusion is needed, making this trivially reachable by an ordinary user any time `IsInboundEnabled` is toggled off.

### Recommendation
Have the UEA contract query and honor the Push Chain-side chain-enabled state (e.g., via a precompile or system-contract call to `x/uregistry`'s `IsChainInboundEnabled`) before executing a payload, or otherwise ensure the pause is enforced at a layer the UEA cannot bypass (e.g., an EVM-level guard checked on every `executeUniversalTx`/`migrateUEA` call, not only in the Cosmos `MsgExecutePayload`/`MsgMigrateUEA` handlers).

### Proof of Concept
1. Governance sets `ChainConfig.Enabled.IsInboundEnabled = false` for chain `eip155:11155111` via `UpdateChainConfig`.
2. Confirm `MsgExecutePayload` is rejected: submitting it returns `"inbound is disabled for chain eip155:11155111"` as shown in the existing test [6](#0-5) .
3. Instead of submitting `MsgExecutePayload`, construct a standard `MsgEthereumTx`/raw transaction targeting the victim's already-deployed UEA address, calling `executeUniversalTx(payload, verificationData)` directly with the same valid pre-signed payload and `verificationData` used in step 2.
4. Submit it via `eth_sendRawTransaction` on the node's exposed JSON-RPC port. Because the UEA contract has no awareness of `IsInboundEnabled`, its own signature/nonce/deadline check passes and the payload executes successfully, confirming the chain-level pause was bypassed.

### Citations

**File:** x/uexecutor/keeper/msg_execute_payload.go (L38-46)
```go
	chainConfig, err := k.uregistryKeeper.GetChainConfig(sdkCtx, caip2Identifier)
	if err != nil {
		return errors.Wrapf(err, "failed to get chain config for chain %s", caip2Identifier)
	}

	if !chainConfig.Enabled.IsInboundEnabled {
		k.Logger().Warn("execute payload rejected: chain inbound disabled", "chain", caip2Identifier)
		return fmt.Errorf("inbound is disabled for chain %s", caip2Identifier)
	}
```

**File:** x/uexecutor/keeper/evm.go (L155-193)
```go
// CallUEAExecutePayload executes a universal payload through UEA
func (k Keeper) CallUEAExecutePayload(
	ctx sdk.Context,
	from, ueaAddr common.Address,
	universal_payload *types.UniversalPayload,
	verificationData []byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	abi, err := types.ParseUeaABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UEA ABI")
	}

	abiUniversalPayload, err := types.NewAbiUniversalPayload(universal_payload)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to create universal payload")
	}

	gasLimit := new(big.Int)
	gasLimit, ok := gasLimit.SetString(universal_payload.GasLimit, 10)
	if !ok {
		return nil, fmt.Errorf("invalid gas limit: %s", universal_payload.GasLimit)
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		gasLimit,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"executeUniversalTx",
		abiUniversalPayload,
		verificationData,
	)
}
```

**File:** x/uexecutor/README.md (L220-236)
```markdown
#### Where authorization actually lives

The cryptographic binding is enforced inside the UEA contract's `executeUniversalTx` (see [`UEA_EVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_EVM.sol#L145) and [`UEA_SVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_SVM.sol)):

1. The contract holds the owner's public key as **immutable bytes** set at UEA deployment via `initialize(_id, _factory)`. There is no code path that mutates this after init.
2. `executeUniversalTx(payload, signature)` verifies the `signature` (passed in as `MsgExecutePayload.VerificationData`) against this stored owner — ECDSA recovery for EVM-origin owners, the Ed25519 precompile (`0x00…00ca`) for SVM-origin owners.
3. The signed payload hash includes a contract-tracked `nonce` (monotonic per UEA) and optional `deadline`, providing replay and freshness protection.
4. If signature verification fails, the contract reverts. The revert propagates as `execErr` from `CallUEAExecutePayload`; the keeper returns the error from `ExecutePayload`; the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. **No state changes survive a failed signature check.**

#### Why this is safe under `Signer ≠ Owner`

An attacker submitting `MsgExecutePayload` with their own `Signer` and a victim's `UniversalAccountId` produces no exploitable outcome:

- The factory resolves the victim's UEA address from the embedded `UniversalAccountId` — correct.
- `evmFrom` (derived from `Signer`) becomes the EVM-level `msg.sender` of the call to the UEA. Since `evmFrom != UNIVERSAL_EXECUTOR_MODULE` (`0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7`), the contract enforces the signature check.
- The attacker cannot forge `VerificationData` that recovers to the victim's owner key.
- The contract reverts → the keeper returns an error → the Cosmos transaction reverts in full.
```

**File:** app/README.md (L193-194)
```markdown
| Default chain ID | `localchain_9000-1` (devnet); testnet uses `push_42101-1` |
| Exposed ports (Docker) | `1317` REST, `26656` P2P, `26657` Tendermint RPC, `8545` EVM JSON-RPC, `8546` EVM WS |
```

**File:** x/uexecutor/types/abi.go (L208-230)
```go
  {
    "type": "function",
    "name": "migrateUEA",
    "inputs": [
      {
        "name": "payload",
        "type": "tuple",
        "internalType": "struct MigrationPayload",
        "components": [
          { "name": "migration", "type": "address", "internalType": "address" },
          { "name": "nonce", "type": "uint256", "internalType": "uint256" },
          { "name": "deadline", "type": "uint256", "internalType": "uint256" }
        ]
      },
      {
        "name": "signature",
        "type": "bytes",
        "internalType": "bytes"
      }
    ],
    "outputs": [],
    "stateMutability": "nonpayable"
  },
```

**File:** test/integration/uexecutor/chain_enabled_test.go (L198-245)
```go
	t.Run("fails when inbound is disabled for the chain", func(t *testing.T) {
		testApp, ctx, _ := utils.SetAppWithValidators(t)

		testApp.UregistryKeeper.AddChainConfig(ctx, &uregistrytypes.ChainConfig{
			Chain:          "eip155:11155111",
			VmType:         uregistrytypes.VmType_EVM,
			PublicRpcUrl:   "https://sepolia.drpc.org",
			GatewayAddress: "0x28E0F09bE2321c1420Dc60Ee146aACbD68B335Fe",
			BlockConfirmation: &uregistrytypes.BlockConfirmation{
				FastInbound:     5,
				StandardInbound: 12,
			},
			GatewayMethods: []*uregistrytypes.GatewayMethods{{
				Name:            "addFunds",
				EventIdentifier: "0xb28f49668e7e76dc96d7aabe5b7f63fecfbd1c3574774c05e8204e749fd96fbd",
			}},
			Enabled: &uregistrytypes.ChainEnabled{
				IsInboundEnabled:  false, // disabled
				IsOutboundEnabled: true,
			},
		})

		ms := uexecutorkeeper.NewMsgServerImpl(testApp.UexecutorKeeper)

		_, err := ms.ExecutePayload(ctx, &uexecutortypes.MsgExecutePayload{
			Signer: "cosmos1xpurwdecvsenyvpkxvmnge3cv93nyd34xuersef38pjnxen9xfsk2dnz8yek2drrv56qmn2ak9",
			UniversalAccountId: &uexecutortypes.UniversalAccountId{
				ChainNamespace: "eip155",
				ChainId:        "11155111",
				Owner:          "0x778d3206374f8ac265728e18e3fe2ae6b93e4ce4",
			},
			UniversalPayload: &uexecutortypes.UniversalPayload{
				To:                   "0x527F3692F5C53CfA83F7689885995606F93b6164",
				Value:                "0",
				Data:                 "0x2ba2ed980000000000000000000000000000000000000000000000000000000000000312",
				GasLimit:             "21000000",
				MaxFeePerGas:         "1000000000",
				MaxPriorityFeePerGas: "200000000",
				Nonce:                "1",
				Deadline:             "0",
				VType:                uexecutortypes.VerificationType(0),
			},
			VerificationData: "0x91987784d56359fa91c3e3e0332f4f0cffedf9c081eb12874a63b41d5b5e5c660dc827947c2ae26e658d0551ad4b2d2aa073d62691429a0ae239d2cc58055bf11c",
		})

		require.Error(t, err)
		require.Contains(t, err.Error(), "inbound is disabled for chain eip155:11155111")
	})
```
