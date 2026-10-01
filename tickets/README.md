# Tickets

Tickets provides simple private support spaces for Red-DiscordBot servers. Pressing a launcher button immediately creates a ticket and returns an ephemeral link; members do not have to complete a modal first.

## Ticket modes

Administrators can enable any combination of:

- **Text** — a permission-isolated text channel.
- **Voice** — a permission-isolated voice channel whose built-in text chat contains the welcome message and controls.
- **Thread** — a private thread under the launcher channel. Discord requires a Level 2 boosted server.

Text and voice tickets may be placed under a configured category or at the top of the channel list. Thread mode requires the bot and configured support roles to have Manage Threads and Send Messages in Threads on the launcher channel. The bot also needs Create Private Threads.

## Privacy model

For text and voice tickets, the bot supplies restrictive overwrites during creation:

- everyone cannot view or connect;
- the requester can view and participate;
- configured staff roles can view and participate;
- the bot can manage the destination and its messages.

The bot verifies effective permissions before posting the welcome message. If verification fails, it deletes the incomplete destination and retains no ticket record.

Private threads add only the requester. Support staff use Manage Threads on the parent launcher channel to access private tickets.

## Setup

Run:

    [p]ticketsset

Use the setup selectors for the launcher channel, optional category, staff roles, and enabled ticket modes. **Edit messages** customizes the launcher title, launcher instructions, and the welcome message posted in every new ticket. Configure the enabled ticket types, then publish:

    [p]ticketsset modes text
    [p]ticketsset modes text voice
    [p]ticketsset modes text voice thread
    [p]ticketsset publish

Additional commands:

    [p]ticketsset channel <channel>
    [p]ticketsset category <category>
    [p]ticketsset clearcategory
    [p]ticketsset staff <role>
    [p]ticketsset log [channel]
    [p]ticketsset limit <1-5>
    [p]ticketsset cooldown <30-3600>

Changing modes or placement does not alter existing tickets. Republish the launcher to update its buttons.

## Ticket use

Members receive an ephemeral link immediately after opening a ticket. The new destination asks them to describe what they need and tells them that support will respond shortly.

Staff can review the bounded queue with:

    [p]tickets queue

Each ticket has persistent controls for claim/unclaim, status, and close/reopen. Closing a text ticket prevents requester messages, closing a voice ticket also prevents requester connections, and closing a thread locks and archives it.

Transcripts, automatic retention deletion, external help-desk integrations, and DM relay are not part of this release.

## Custom topic launcher

Switch from the generic mode buttons to recognizable support topics by adding buttons. Each topic uses a 2-10 character lowercase prefix and maps to text, voice, or thread mode:

    [p]ticketsset topic add mine text ⛏️ Minecraft Support
    [p]ticketsset topic add web text 🌐 Website Support
    [p]ticketsset topic add disc text 💬 Discord Support
    [p]ticketsset topic
    [p]ticketsset topic remove mine
    [p]ticketsset style generic
    [p]ticketsset style custom

Custom destinations use names such as `sup-mine-0007`. Adding a topic automatically enables its destination mode. Republish after changing topics or launcher style. Requester closure removes their channel access; staff can reopen a ticket or permanently delete it with confirmation.
