# Tickets

Tickets provides private support channels for Red-DiscordBot servers without requiring Discord boosts or private threads.

## Privacy model

The configured public support channel contains only an Open Ticket launcher. A requester enters the topic, subject, and support message in a modal. The bot creates a normal text channel under the configured ticket category with restrictive permission overwrites supplied during channel creation:

- everyone cannot view the channel;
- the requester can view and reply;
- configured staff roles can view and reply;
- the bot can manage the channel and messages.

The bot verifies these permissions before posting the support message. If verification fails, it deletes the empty incomplete channel and does not retain a ticket record.

## Setup

Run:

    [p]ticketsset

Use the selectors to choose a launcher channel, ticket category, staff roles, and optional staff log. Then press Publish launcher.

Commands are also available:

    [p]ticketsset channel <channel>
    [p]ticketsset category <category>
    [p]ticketsset staff <role>
    [p]ticketsset log [channel]
    [p]ticketsset limit <1-5>
    [p]ticketsset cooldown <30-3600>
    [p]ticketsset publish

## Ticket use

Members normally open tickets through the launcher. Staff can review the bounded queue with:

    [p]tickets queue

Each ticket has persistent controls for claim/unclaim, status, and close/reopen. Closing removes the requesters ability to send while preserving read access.

Private threads, public forum tickets, transcripts, and external help-desk services are not part of the first release.
