# Q2464: TransferNFT owner binding against recipient balance

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on owner index membership, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer from or to an escrow/module address and test later refunds, causing `recipient balance` to diverge so the invariant `only current owner can transfer the NFT` fails and the attacker can transfer a victim NFT by confusing signer or stale owner index state, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: transfer a victim NFT by confusing signer or stale owner index state by testing the owner binding angle against `recipient balance` during `transfer from or to an escrow/module address and test later refunds`, with specific focus on owner index membership.
- Invariant to test: only current owner can transfer the NFT; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
