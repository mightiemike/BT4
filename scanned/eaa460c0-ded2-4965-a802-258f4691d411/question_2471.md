# Q2471: TransferNFT owner binding against escrow address ownership

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on owner index membership, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer via authz/feegrant path and compare signer to sender, causing `escrow address ownership` to diverge so the invariant `collection state remains single-owner after rapid transfer/send ordering` fails and the attacker can create two apparent owners and use one path to bridge while the other sells or burns, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: create two apparent owners and use one path to bridge while the other sells or burns by testing the owner binding angle against `escrow address ownership` during `transfer via authz/feegrant path and compare signer to sender`, with specific focus on owner index membership.
- Invariant to test: collection state remains single-owner after rapid transfer/send ordering; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
