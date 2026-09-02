"use strict";

document.documentElement.dataset.walletUi = "ready";

const statusElement = document.querySelector("#session-status");
const detailsElement = document.querySelector("#session-details");

function addDetail(label, value) {
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  detailsElement.append(term, detail);
}

if (statusElement && detailsElement) {
  fetch("api/session.php", { credentials: "same-origin", headers: { Accept: "application/json" } })
    .then(async (response) => {
      const body = await response.json();
      if (!response.ok) throw new Error(body.error?.message || "Wallet session unavailable.");
      return body.data;
    })
    .then((session) => {
      statusElement.textContent = "Discord identity verified.";
      addDetail("Purpose", session.purpose);
      addDetail("Expires", new Date(session.expires_at * 1000).toLocaleString());
      if (session.transaction) {
        addDetail("Network", `${session.transaction.network_name} (${session.transaction.chain_id})`);
        addDetail("From", session.transaction.from_address);
        addDetail("To", session.transaction.to_address);
        addDetail("Value (wei)", session.transaction.value_wei);
        addDetail("Intent", session.transaction.intent_id);
      }
      detailsElement.hidden = false;
    })
    .catch((error) => {
      statusElement.textContent = error.message;
    });
}
