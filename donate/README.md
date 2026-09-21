# Donate

Donate is a lightweight Red-DiscordBot cog for displaying configured donation options.

It does not verify payments, track donation history, or assign donor roles. Verified account upgrades and donor role sync should live in a separate integration.

The default donation card includes SickGaming's donation page, PayPal, Patreon, BTC, ETH, and LTC donation options. The donation page is pinned first, and the other methods are alphabetical. Server admins can replace, remove, or order methods with `donateset`.

## Commands

- `donate` shows the configured donation embed.
- `donations` and `support` are aliases for `donate`.
- `donateset` shows admin configuration commands.
- `donateset setup` opens the interactive setup dashboard (`interactive` is an alias).

## Setup

Configure the donation card from Discord:

```text
donateset setup
```

The dashboard previews the public card and provides forms for its title, description, footer, donation methods, display order, and notes. Existing values—including the default card—are prefilled. Removing a method or note and restoring all defaults require confirmation. Submitted forms save immediately; **Done** closes the dashboard.

The text commands remain available for quick edits and automation:

```text
donateset view
donateset title Support SickGaming
donateset description Donations help keep the community and servers running.
donateset method paypal PayPal | https://paypal.me/example
donateset method cashapp Cash App | $example
donateset method venmo Venmo | @example
donateset method eth Ethereum | 0xYourAddress | ETH, Base, or supported EVM networks only.
donateset note add Donations are optional and never required to participate.
```

Then users can run:

```text
donate
donations
support
```

## Managing Methods

Each method has a key, label, value, and optional note:

```text
donateset method <key> <label> | <value> [| note]
```

Examples:

```text
donateset method paypal PayPal | https://paypal.me/example
donateset method btc Bitcoin | bc1qexampleaddress | BTC only.
donateset method base Base/Ethereum | 0xYourAddress | Base or ETH only.
```

Remove a method:

```text
donateset remove paypal
```

Pin a method to the top of the list:

```text
donateset order donation-page 1
```

Clear a pinned order so the method returns to alphabetical sorting:

```text
donateset order paypal 0
```

Reset all settings:

```text
donateset clear
```

## Notes

Donation notes appear at the bottom of the public embed.

```text
donateset note add Please double-check wallet addresses before sending crypto.
donateset note remove 1
```
