// self-buy.mjs — make ONE real $0.02 paid call to Extractr on Base mainnet.
// Purpose: the first settled payment through the CDP facilitator triggers
// agentic.market auto-indexing (~6h refresh). Run this once, then delete it.
//
// Setup (his machine, his wallet):
//   1. npm install @x402/fetch @x402/evm viem
//   2. Fund a Base wallet with ~$0.05 USDC (covers the $0.02 price) + a few
//      cents of ETH for Base gas. Any Base wallet works; it does NOT need to
//      be the payTo wallet.
//   3. PRIVATE_KEY=0x... node self-buy.mjs
//      (export the key from MetaMask: Account details > Show private key.
//       Never paste it in chat, never commit it anywhere.)
//
// Safety: the client is capped at $0.10 per call via spendControls.

import { privateKeyToAccount } from "viem/accounts";
import { wrapFetchWithPaymentFromConfig } from "@x402/fetch";
import { ExactEvmScheme } from "@x402/evm";

const SERVICE_URL = "https://extractr-x402-production.up.railway.app";

const privateKey = process.env.PRIVATE_KEY;
if (!privateKey || !privateKey.startsWith("0x")) {
  console.error("Set PRIVATE_KEY=0x... (Base wallet, a few cents of USDC + ETH).");
  process.exit(1);
}

const account = privateKeyToAccount(privateKey);

const fetchWithPayment = wrapFetchWithPaymentFromConfig(fetch, {
  schemes: [
    {
      network: "eip155:8453", // Base mainnet
      client: new ExactEvmScheme(account),
    },
  ],
  spendControls: { maxAmountPerPayment: "$0.10" }, // safety cap per call
});

const res = await fetchWithPayment(`${SERVICE_URL}/v1/extract`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ url: "https://example.com", summary_sentences: 3 }),
});

console.log("HTTP", res.status);
const data = await res.json();
console.log(JSON.stringify(data, null, 2));

const receipt = res.headers.get("PAYMENT-RESPONSE");
if (receipt) {
  console.log("\nPayment receipt header present — payment settled through the facilitator.");
  console.log("If this is the first settled payment, agentic.market should auto-index extractr within ~6h.");
}
