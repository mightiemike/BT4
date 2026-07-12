# Q2384: TransferNFT owner binding against collection index

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on denom owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer from or to an escrow/module address and test later refunds, causing `collection index` to diverge so the invariant `signer, sender, and authz grant cannot diverge to transfer victim NFTs` fails and the attacker can use authz/feegrant message construction to authorize transfer from the wrong sender, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: use authz/feegrant message construction to authorize transfer from the wrong sender by testing the owner binding angle against `collection index` during `transfer from or to an escrow/module address and test later refunds`, with specific focus on denom owner authority.
- Invariant to test: signer, sender, and authz grant cannot diverge to transfer victim NFTs; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
