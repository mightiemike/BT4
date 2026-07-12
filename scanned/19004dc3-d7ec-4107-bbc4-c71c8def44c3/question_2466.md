# Q2466: TransferNFT owner binding against recipient balance

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on owner index membership, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer via authz/feegrant path and compare signer to sender, causing `recipient balance` to diverge so the invariant `escrow-owned NFTs cannot be moved by a non-escrow account` fails and the attacker can create two apparent owners and use one path to bridge while the other sells or burns, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: create two apparent owners and use one path to bridge while the other sells or burns by testing the owner binding angle against `recipient balance` during `transfer via authz/feegrant path and compare signer to sender`, with specific focus on owner index membership.
- Invariant to test: escrow-owned NFTs cannot be moved by a non-escrow account; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
