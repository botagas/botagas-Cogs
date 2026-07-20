# botagas-Cogs V3

[![CodeQL](https://github.com/botagas/botagas-Cogs/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/botagas/botagas-Cogs/blob/main/.github/workflows/codeql-analysis.yml)
[![Linting](https://github.com/botagas/botagas-Cogs/actions/workflows/tests.yml/badge.svg)](https://github.com/botagas/botagas-Cogs/blob/main/.github/workflows/tests.yml)

Custom cogs for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot), created and maintained by **botagas**.  
Originally forked from [Seina-Cogs](https://github.com/japandotorg/Seina-Cogs) and [Dav-Cogs](https://github.com/Dav-Git/Dav-Cogs), this repo introduces modern reworks for **Captcha** and **Roomer** with full support for **slash commands**, **modals**, and **interactive UIs**.

---

## 🔧 Installation

#### Replace `[p]` with your bot’s command prefix.

```bash
[p]load downloader
[p]repo add botagas-Cogs https://github.com/botagas/botagas-Cogs
[p]cog install botagas-Cogs <cogname>
[p]load <cogname>
```
#### View, enable and sync slash commands

```bash
[p]slash list
[p]slash enablecog <cog>
[p]slash sync
```
> [!NOTE]
> You may need to restart your Discord client to see the newly added slash commands.

---

## 🧩 Available Cogs

### 🏠 Roomer (fully reworked)
Roomer now supports **slash commands**, **interactive buttons**, **modals**, **select menus**, and **game presets** for managing temporary voice channels.

#### Required Category Permissions
- `View Channels` - To view channels
- `Manage Channels` - To update, edit, delete channels
- `Manage Permissions` - To alter permissions for roles/users in the channels
- `Send Messages` - To send messages in voice channels
- `Embed Links` - To send embeds in voice channels
- `Connect` - To connect to channels and alter them
- `Set Voice Channel Status` - To set channel activity status
- `Move Members` - To move members to their respective channels

#### Slash Commands:
- `/roomer add <channel>` - Set `Join to Create` channel
- `/roomer remove <channel>` - Remove `Join to Create` channel
- `/roomer channels` - List configured `Join to Create` channels
- `/roomer enable` - Enable automatic temporary channel creation
- `/roomer disable` - Disable automatic temporary channel creation
- `/roomer preset` — Manage game presets (`add`, `edit`, `delete`, `list`)

#### Core Features:
- Automatically creates voice channels when users join a **Join-to-Create (JTC)** channel
- Sends an interactive control panel to the room’s text chat with:
  - 🔒 **Lock / Unlock** — Toggle `@everyone`'s `connect` permission
  - 🙈 **Hide / Unhide** — Toggle `@everyone`'s `view_channel` permission
  - ➕ **Permit** — Grant access to selected members or roles
  - ➖ **Forbid** — Deny access to selected members or roles
  - ✏️ **Rename** — Change the voice channel name
  - 📝 **Set Status** — Set a voice channel status
  - 👥 **Set Limit** — Max user cap (up to 99)
  - 🔄 **Reset Channel** — Revert to default settings from the JTC category
  - 🧹 **Clear Permissions** - Revert permission overwrites while keeping channel customisation
  - 🎙 **Claim Room** — Claim ownership if the owner is no longer present
  - 🎮 **Channel Preset** — Apply a predefined channel preset (sets `title`, `status`, `limit`)

#### Game Presets:
- Admins can define game presets with `/roomer preset` `add` / `edit` / `delete`
- Users can apply presets from the UI dropdown after pressing `🎮 Channel Preset`
- Each preset includes:
  - Title (required)
  - Status (optional)
  - Limit (optional)

#### New Behavior:
- Channels are auto-deleted **after 10 seconds of inactivity**
  - Channels are deleted only when they are empty
  - Channel checks are triggered on voice state update
  - Even if the user is now present in another temporary channel right after leaving the previous one, the old channel is deleted if empty
- Reset syncs permissions with the **parent category**
- Lock/Hide buttons reflect the **current state** of the `connect` / `view_channel` permissions
- Supports **dynamic label updates** on buttons after interaction for `Lock` and `Hide` buttons

#### WIP:
- Full automatic translation support using **Red’s translation system** via github actions

---

### 🧪 Captcha (fully reworked)
The new Captcha system uses **slash commands**, **modals**, and **UI buttons**. No more clutter from join messages and replies.

#### Slash Commands:
- `/captcha channel <channel>` — Set the verification channel
- `/captcha toggle` — Enable/disable verification
- `/captcha deploy` — Deploy the verification embed
- `/captcha role <VerifiedRole>` — Role to assign upon verification
- `/captcha unverifiedrole <UnverifiedRole>` — Role before verification
- `/captcha tries <number>` — Number of attempts allowed
- `/captcha timeout <seconds>` — How long the captcha is valid
- `/captcha embed <text>` — Set message for the embed
- `/captcha before <text>` — Message shown before captcha
- `/captcha after <text>` — Message shown after success

#### Features:
- Verification message is persistent and interactive
- DM-enabled users receive an image captcha
- DM-disabled users fallback to modal-based verification
- Captchas automatically **expire and invalidate** after timeout
- Users can retry if captcha expires or fails
- Cleans up messages automatically
- Supports **custom before/after/embed messages**

#### Known Limitation / WIP:
- Currently, none.

---

### 📣 RoomAnnounce (Roomer companion)

RoomAnnounce adds persistent game-announcement controls to every temporary Roomer channel.
It can detect Discord Rich Presence, use Roomer presets, enrich games through IGDB, and use
SteamGridDB as an optional artwork fallback.

#### Setup

- Install and load `roomer` before `roomannounce`.
- Enable Discord's **Guild Presences** privileged intent for automatic game detection.
- `/roomannounce channel [channel] [join_to_create]` — Set or clear the default destination or a Join-to-Create-specific destination.
- `/roomannounce channels` — List Join-to-Create channels and their effective announcement destinations.
- `/roomannounce autoannounce <enabled>` — Toggle automatic publishing for Roomer-created rooms (disabled by default).
- `/roomannounce autotag <enabled>` — Toggle automatic role tagging (disabled by default).
- `/roomannounce autohide <enabled>` — Hide empty announcement destinations and reveal them before a new post (disabled by default).
- `/roomannounce autohiderole [role]` — Change visibility for a specific member role; omit the role to use `@everyone`.
- `/roomannounce activehours set <timezone> <start> <end> [weekdays]` — Schedule hours when automatic role tags are allowed.
- `/roomannounce activehours enable|force|clear|settings` — Toggle, enforce, clear, or inspect active hours.
- `/roomannounce rsvp enable|names|settings` — Configure persistent announcement RSVP controls.
- `/roomannounce monitor add <voice_channel> <announcement_channel>` — Monitor a static voice channel for detected games.
- `/roomannounce monitor remove|list|enable` — Remove, list, enable, or disable static-channel monitoring.
- `/roomannounce provider <provider> <enabled>` — Enable IGDB or SteamGridDB per server.
- `/roomannounce lookup <game_name>` — Test exact-title provider matching and credential/API status.
- `/roomannounce role <add|remove|list> [role]` — Manage roles owners may select.
- `/roomannounce settings` — Show the current configuration.

IGDB credentials are stored through Red's shared Twitch API tokens:

```bash
[p]set api twitch client_id,<id> client_secret,<secret>
```

SteamGridDB artwork requires its own API key:

```bash
[p]set api steamgriddb api_key,<key>
```

#### Behavior

- Every Roomer channel gets a separate announcement control panel.
- Local previews and public announcements are separate messages.
- Owners can edit detected data, choose a preset, select an approved role, or enter a game manually.
- Announcements can be disabled for the entire lifetime of an individual room.
- Missing roles never result in an unintended mention; the owner is warned in the voice channel.
- Public announcements and stored state are cleaned up when the Roomer channel is deleted.
- Public embeds omit unavailable optional data; local previews retain missing-data and provider diagnostics.
- Automatically fetched provider descriptions are reduced to their first sentence; preset and manual descriptions remain unchanged.
- Active hours suppress automatic role pings without stopping informational announcement updates; forced hours also block manual pings.
- Active-hour weekdays are comma-separated names such as `Mon,Wed,Fri`; overnight windows use the weekday on which the window starts.
- Optional RSVP buttons track Join, Maybe, and Not Coming responses and notify room owners at configured participation milestones.
- Optional destination auto-hide preserves the configured role's original visibility, waits briefly after removals, and hides the channel only when no Roomer or monitored announcement remains. It defaults to `@everyone`, and the bot needs **Manage Roles** in each destination.
- Channels already hidden by an administrator are not claimed or automatically revealed by RoomAnnounce.
- RSVP summaries start on their own embed row, and a separate **Connect** link opens the advertised room in Discord.
- Hiding a Roomer channel suspends its public post and role pings while retaining RSVP data; unhiding restores the same announcement session.
- Locked rooms remain advertised, but show a disabled **Locked** control and cannot send new role pings until unlocked.
- Monitored static channels publish one automatic informational post per distinct detected game, without Roomer controls, presets, RSVP, or role tags.
- Monitored posts count non-bot members detected playing that game separately from total non-bot voice-room occupancy, and disappear when the game is no longer active.
- **In Room** shows only the current participant count for unlimited voice channels and current/maximum for limited channels.

---

## 🙌 Credits

- **[Seina-Cogs](https://github.com/japandotorg/Seina-Cogs)** — for original Captcha logic
- **[Dav-Cogs](https://github.com/Dav-Git/Dav-Cogs)** — for original Roomer logic
- Thanks to the [Red Discord Bot](https://discord.gg/red) community for feedback and the Red Bot itself

---

## 💬 Need Help?

Join [`Red - Cog Support`](https://discord.gg/GET4DVk) for help or questions.  
I don’t have my own support channel, but you may find me there as **`@Winter`**. GitHub issue reports are welcome.
