# Q2613: BurnNFT owner binding against IBC voucher backing

## Question
Can NFT owner enter through `msgServer.BurnNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, DenomId, Id, escrow ownership state, focusing on owner index membership, under the precondition that a valuable denom or NFT exists under the NFT module, then burn after transfer in the same block ordering model, causing `IBC voucher backing` to diverge so the invariant `burning a voucher cannot allow later duplicate refund minting` fails and the attacker can burn a voucher but still receive a refund that mints a duplicate backed asset, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.BurnNFT
- Entrypoint: Cosmos SDK MsgBurnNFT transaction
- Attacker controls: Sender, DenomId, Id, escrow ownership state
- Exploit idea: burn a voucher but still receive a refund that mints a duplicate backed asset by testing the owner binding angle against `IBC voucher backing` during `burn after transfer in the same block ordering model`, with specific focus on owner index membership.
- Invariant to test: burning a voucher cannot allow later duplicate refund minting; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over BurnNFT plus nft-transfer refund and timeout state; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
