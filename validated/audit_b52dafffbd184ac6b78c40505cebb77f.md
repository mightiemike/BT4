### Title
`MsgUpdateParams` for `x/inflation` Is Permanently Unreachable Because Its Authority Is the Inflation Module Account, Which Cannot Sign Transactions - (`x/inflation/keeper/msg_server.go`, `x/inflation/keeper/keeper.go`)

---

### Summary

The `x/inflation` module exposes a `MsgUpdateParams` message to update critical inflation parameters (supply cap, decay rate, blocks-per-year). The message server enforces that only the **inflation module account** may be the signer. However, a Cosmos SDK module account has no private key and cannot sign or submit transactions. No governance proposal path exists that would satisfy this authority check. As a result, inflation parameters can never be updated after genesis — the `MsgUpdateParams` handler is permanently unreachable.

---

### Finding Description

The inflation keeper stores an `authority` string and enforces it on every `UpdateParams` call: [1](#0-0) [2](#0-1) 

The proto specification is explicit about what that authority must be:

> *"The authority is hard-coded to the x/inflation module account."* [3](#0-2) 

The keeper comment repeats this intent:

> *"the address capable of executing a MsgUpdateParams message. Typically, this should be the x/inflation module account."* [1](#0-0) 

A Cosmos SDK module account (`authtypes.NewModuleAddress("inflation")`) is a deterministic address with **no private key**. It cannot sign a `sdk.Tx`. Therefore:

- A direct user transaction cannot satisfy the authority check.
- A governance proposal cannot satisfy it either, because governance proposals are executed under the **gov module account** address — a different address from the inflation module account.

Contrast this with `x/tieredrewards`, where the authority is explicitly set to the **gov module account**: [4](#0-3) [5](#0-4) 

The tieredrewards CLI wraps authority-gated messages in governance proposals using `govAuthorityAddress()`, which resolves to the gov module account — the same address that governance execution injects. This path works. The inflation module has no equivalent path. [6](#0-5) 

---

### Impact Explanation

The parameters controlled by `MsgUpdateParams` include `MaxSupply` (the hard supply cap), `DecayRate`, and `BlocksPerYear`: [7](#0-6) 

If any of these need to be adjusted post-genesis — for example, to correct a misconfigured supply cap or to disable/adjust inflation decay — there is no on-chain mechanism to do so. The `MsgUpdateParams` handler exists in the binary and is registered, but it can never be successfully invoked. The inflation parameters are permanently frozen at their genesis values.

The corrupted invariant is: **the `MaxSupply` and `DecayRate` state values in the inflation module store can never be updated through any supported production transaction path**, even though the module exposes a message type explicitly designed for that purpose.

---

### Likelihood Explanation

The flaw is structural and deterministic. Any attempt to call `MsgUpdateParams` — whether directly or via a governance proposal — will fail with `ErrInvalidSigner` because no reachable signer can produce the inflation module account address as the transaction signer. The likelihood of the broken state being triggered is 100% whenever an operator or governance participant attempts to update inflation parameters.

---

### Recommendation

Change the authority passed to `inflation.NewKeeper` in `app/app.go` from the inflation module account to the governance module account, matching the pattern used by `x/tieredrewards`:

```go
// In app/app.go, when constructing the inflation keeper:
authority := authtypes.NewModuleAddress(govtypes.ModuleName).String()
app.InflationKeeper = inflationkeeper.NewKeeper(
    appCodec, keys[inflationtypes.StoreKey], logger,
    app.BankKeeper, app.StakingKeeper,
    authority, // <-- use gov module account, not inflation module account
)
```

Also update the proto comment and keeper comment to reflect the corrected authority. Add a CLI command analogous to `GetCmdUpdateParamsProposal()` in `x/tieredrewards/client/cli/tx.go` so that governance participants can submit `MsgUpdateParams` proposals for the inflation module. [8](#0-7) 

---

### Proof of Concept

1. On a running chain, query the current inflation params:
   ```
   chain-maind query inflation params
   ```
2. Attempt to submit a governance proposal to update them, using the gov module account as authority (the only account governance can inject):
   ```json
   {
     "messages": [{
       "@type": "/chainmain.inflation.v1.MsgUpdateParams",
       "authority": "<gov-module-account-address>",
       "params": { ... }
     }]
   }
   ```
3. The proposal passes on-chain vote. During execution, `msg_server.go` compares `k.authority` (inflation module account) against `msg.Authority` (gov module account). They differ → `ErrInvalidSigner` is returned → the proposal execution fails.
4. Attempt with the inflation module account address as authority: no private key exists for this address, so no valid signature can be produced → transaction is rejected at the ante-handler level.
5. Result: `MsgUpdateParams` is permanently unreachable; inflation parameters remain frozen at genesis values. [2](#0-1) [3](#0-2)

### Citations

**File:** x/inflation/keeper/keeper.go (L26-28)
```go
	// the address capable of executing a MsgUpdateParams message. Typically, this
	// should be the x/inflation module account.
	authority string
```

**File:** x/inflation/keeper/msg_server.go (L26-39)
```go
// UpdateParams implements MsgServer.UpdateParams method.
// It defines a method to update the x/inflation module parameters.
func (k msgServer) UpdateParams(goCtx context.Context, msg *types.MsgUpdateParams) (*types.MsgUpdateParamsResponse, error) {
	if k.authority != msg.Authority {
		return nil, errors.Wrapf(govtypes.ErrInvalidSigner, "invalid authority; expected %s, got %s", k.authority, msg.Authority)
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := k.SetParams(ctx, msg.Params); err != nil {
		return nil, err
	}

	return &types.MsgUpdateParamsResponse{}, nil
}
```

**File:** proto/chainmain/inflation/v1/tx.proto (L16-18)
```text
  // UpdateParams defines a governance operation for updating the x/inflation module
  // parameters. The authority is hard-coded to the x/inflation module account.
  rpc UpdateParams(MsgUpdateParams) returns (MsgUpdateParamsResponse);
```

**File:** proto/chainmain/tieredrewards/v1/tx.proto (L19-21)
```text
  // UpdateParams defines a governance operation for updating the x/tieredrewards module
  // parameters. The authority is hard-coded to the x/gov module account.
  rpc UpdateParams(MsgUpdateParams) returns (MsgUpdateParamsResponse);
```

**File:** x/tieredrewards/client/cli/tx.go (L149-165)
```go
func GetCmdUpdateParamsProposal() *cobra.Command {
	return newGovProposalCmd(
		"update-params-proposal [params]",
		"Submit a proposal to update tieredrewards parameters",
		func(clientCtx client.Context, arg string) (sdk.Msg, error) {
			params, err := parseParamsArg(clientCtx, arg)
			if err != nil {
				return nil, err
			}

			return &types.MsgUpdateParams{
				Authority: govAuthorityAddress(),
				Params:    params,
			}, nil
		},
	)
}
```

**File:** x/tieredrewards/client/cli/tx.go (L484-486)
```go
func govAuthorityAddress() string {
	return authtypes.NewModuleAddress(govtypes.ModuleName).String()
}
```
