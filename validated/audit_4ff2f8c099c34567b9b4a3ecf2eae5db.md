### Title
Universal Account Migration Signature Replay across Chains and Contracts - (`x/uexecutor/keeper/msg_migrate_uea.go`)

### Summary
The `MigrateUEA` functionality in Push Chain allows users to migrate their Universal Entity Account (UEA) by providing a signed `MigrationPayload`. However, the `MigrationPayload` structure and the associated `migrateUEA` EVM call do not explicitly include or verify the `chainId` or the specific UEA contract address within the signed data. This allows an unprivileged attacker to capture a valid migration signature from one chain (e.g., a testnet) and replay it on another chain (e.g., mainnet) where the user has the same UEA address, or across different contract instances, potentially leading to unauthorized account takeovers or state transitions.

### Finding Description
In `x/uexecutor/keeper/msg_migrate_uea.go`, the `MigrateUEA` function facilitates UEA migration [1](#0-0) . It validates the `migrationPayload` [2](#0-1)  and then calls the `migrateUEA` function on the UEA contract via the EVM [3](#0-2) .

The `MigrationPayload` struct, as defined in the ABI and the Go types, only contains the `migration` address, a `nonce`, and a `deadline` [4](#0-3) [5](#0-4) . It lacks a `chainId` field and the address of the contract being migrated. While the UEA contract provides a `domainSeparator` function [6](#0-5) , the `MigrateUEA` handler in the keeper does not enforce or verify that the signature is bound to the current chain's identity before initiating the EVM execution. Since UEA addresses are deterministic and likely identical across chains, a signature valid on one chain is cryptographically valid for the same UEA on any other chain.

### Impact Explanation
An attacker can perform a cross-chain replay attack. If a user signs a migration request on a less secure or test chain, the attacker can capture the `MigrationPayload` and `signature` and submit them to the `MigrateUEA` message on a production chain. This results in the unauthorized migration of the user's UEA to an attacker-controlled address on the production chain, leading to a permanent loss of the user's account and associated assets. This constitutes an unauthorized UEA execution and state transition [7](#0-6) .

### Likelihood Explanation
The likelihood is medium-high. Users often interact with multiple chains (testnets and mainnets) using the same keys and addresses. Since the `MigrateUEA` message is a public unprivileged entry point, any observer can see the transaction data and attempt a replay. The deterministic nature of UEA addresses across chains makes this attack highly feasible if domain separation is not strictly enforced in the signature verification logic.

### Recommendation
1.  **Include Chain ID and Contract Address**: Update the `MigrationPayload` struct to include `chainId` and `ueaAddress`.
2.  **Enforce EIP-712**: Ensure the UEA contract's `migrateUEA` implementation uses the `domainSeparator` to verify that the signature is intended for the specific `chainId` and contract `address(this)`.
3.  **Keeper-side Validation**: In `x/uexecutor/keeper/msg_migrate_uea.go`, verify that the `migrationPayload` is intended for the current `ctx.ChainID()` before calling the EVM.

### Proof of Concept
1.  A user has a UEA at address `0xUEA...` on both Chain A (Testnet) and Chain B (Mainnet).
2.  The user signs a `MigrationPayload` to migrate their account on Chain A to `0xNewOwner`.
    *   `payload = {migration: 0xNewOwner, nonce: 1, deadline: 9999999999}`
    *   `signature = sign(payload)`
3.  The attacker observes the transaction on Chain A and extracts the `payload` and `signature`.
4.  The attacker constructs a `MsgMigrateUEA` for Chain B using the same `payload` and `signature`.
5.  The Push Chain node on Chain B accepts the message because the `payload` and `signature` are cryptographically valid for `0xUEA...`, and there is no `chainId` check in the payload.
6.  The UEA on Chain B is migrated to `0xNewOwner` without the user's consent for that specific chain.

### Citations

**File:** x/uexecutor/keeper/msg_migrate_uea.go (L15-15)
```go
func (k Keeper) MigrateUEA(ctx context.Context, evmFrom common.Address, universalAccountId *types.UniversalAccountId, migrationPayload *types.MigrationPayload, signature string) error {
```

**File:** x/uexecutor/keeper/msg_migrate_uea.go (L28-31)
```go
	_, err := types.NewAbiMigrationPayload(migrationPayload)
	if err != nil {
		return errors.Wrapf(err, "invalid migration payload")
	}
```

**File:** x/uexecutor/keeper/msg_migrate_uea.go (L71-74)
```go
	receipt, err := k.CallUEAMigrateUEA(sdkCtx, evmFrom, ueaAddr, migrationPayload, signatureVal)
	if err != nil {
		return err
	}
```

**File:** x/uexecutor/types/abi.go (L211-221)
```go
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
```

**File:** x/uexecutor/types/abi.go (L233-240)
```go
    "type": "function",
    "name": "domainSeparator",
    "inputs": [],
    "outputs": [
      { "name": "", "type": "bytes32", "internalType": "bytes32" }
    ],
    "stateMutability": "view"
  }
```

**File:** x/uexecutor/types/abi.go (L922-926)
```go
type AbiMigrationPayload struct {
	Migration common.Address
	Nonce     *big.Int
	Deadline  *big.Int
}
```
