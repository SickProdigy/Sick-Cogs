const copyButton = document.querySelector("#copy-setup-command");
const command = document.querySelector("#setup-command");
const status = document.querySelector("#copy-setup-status");

if (copyButton && command && status) {
  copyButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(command.textContent || "");
      copyButton.textContent = "Copied";
      status.textContent = "Command copied. Clear your clipboard after saving the secret in Red.";
    } catch {
      status.textContent = "Copy failed. Select the wrapped command and copy it manually.";
    }
  });
}
