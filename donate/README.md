# Donate

Donate is a lightweight Red-DiscordBot cog for displaying configured donation options.

It does not verify payments, track donation history, or assign donor roles. Verified account upgrades and donor role sync should live in a separate integration.

The default donation card includes SickGaming's donation page, PayPal, Patreon, BTC, ETH, and LTC donation options. The donation page is pinned first, and the other methods are alphabetical. Server admins can replace, remove, or order methods with `donate set`.

## Commands

- `donate` shows the configured donation embed.
- `donations` and `support` are aliases for `donate`.
- `donate set` shows admin configuration commands.

## Setup

Configure the donation card from Discord:

```text
donate set view
donate set title Support SickGaming
donate set description Donations help keep the community and servers running.
donate set method paypal PayPal | https://paypal.me/example
donate set method cashapp Cash App | $example
donate set method venmo Venmo | @example
donate set method eth Ethereum | 0xYourAddress | ETH, Base, or supported EVM networks only.
donate set note add Donations are optional and never required to participate.
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
donate set method <key> <label> | <value> [| note]
```

Examples:

```text
donate set method paypal PayPal | https://paypal.me/example
donate set method btc Bitcoin | bc1qexampleaddress | BTC only.
donate set method base Base/Ethereum | 0xYourAddress | Base or ETH only.
```

Remove a method:

```text
donate set remove paypal
```

Pin a method to the top of the list:

```text
donate set order donation-page 1
```

Clear a pinned order so the method returns to alphabetical sorting:

```text
donate set order paypal 0
```

Reset all settings:

```text
donate set clear
```

## Notes

Donation notes appear at the bottom of the public embed.

```text
donate set note add Please double-check wallet addresses before sending crypto.
donate set note remove 1
```
