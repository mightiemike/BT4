[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** x/uexecutor/types/abi.go (L255-268)
```go
    {
      "type": "function",
      "name": "depositPRC20WithAutoSwap",
      "inputs": [
        { "name": "prc20", "type": "address", "internalType": "address" },
        { "name": "amount", "type": "uint256", "internalType": "uint256" },
        { "name": "target", "type": "address", "internalType": "address" },
        { "name": "fee", "type": "uint24", "internalType": "uint24" },
        { "name": "minPCOut", "type": "uint256", "internalType": "uint256" },
        { "name": "deadline", "type": "uint256", "internalType": "uint256" }
      ],
      "outputs": [],
      "stateMutability": "nonpayable"
    },
```

**File:** x/uexecutor/types/abi.go (L383-389)
```go
    {
      "type": "function",
      "name": "defaultFeeTier",
      "inputs": [{ "name": "", "type": "address", "internalType": "address" }],
      "outputs": [{ "name": "", "type": "uint24", "internalType": "uint24" }],
      "stateMutability": "view"
    },
```

**File:** x/uexecutor/types/abi.go (L958-965)
```go
// AbiQuoteExactInputSingleParams matches IQuoterV2.QuoteExactInputSingleParams
type AbiQuoteExactInputSingleParams struct {
	TokenIn           common.Address
	TokenOut          common.Address
	AmountIn          *big.Int
	Fee               *big.Int // uint24
	SqrtPriceLimitX96 *big.Int // uint160, 0 = no limit
}
```

**File:** x/uexecutor/keeper/evm.go (L1-1)
```go
package keeper
```
