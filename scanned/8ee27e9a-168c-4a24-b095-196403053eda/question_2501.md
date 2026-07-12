# Q2501: TransferNFT owner binding against NFT owner

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on token identity preservation, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer via authz/feegrant path and compare signer to sender, causing `NFT owner` to diverge so the invariant `collection state remains single-owner after rapid transfer/send ordering` fails and the attacker can create two apparent owners and use one path to bridge while the other sells or burns, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: create two apparent owners and use one path to bridge while the other sells or burns by testing the owner binding angle against `NFT owner` during `transfer via authz/feegrant path and compare signer to sender`, with specific focus on token identity preservation.
- Invariant to test: collection state remains single-owner after rapid transfer/send ordering; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
