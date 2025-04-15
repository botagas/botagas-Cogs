# botagas-Cogs V3

[![CodeQL](https://github.com/botagas/botagas-Cogs/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/botagas/botagas-Cogs/blob/main/.github/workflows/codeql-analysis.yml)
[![Linting](https://github.com/botagas/botagas-Cogs/actions/workflows/tests.yml/badge.svg)](https://github.com/botagas/botagas-Cogs/blob/main/.github/workflows/tests.yml)

Custom cogs for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot), created and maintained by **botagas**.  
Originally forked from [Seina-Cogs](https://github.com/japandotorg/Seina-Cogs) and [Dav-Cogs](https://github.com/Dav-Git/Dav-Cogs), this repo introduces modern reworks for **Captcha** and **Roomer** with full support for **slash commands**, **modals**, and **interactive UIs**.

---

## 🔧 Installation

> Replace `[p]` with your bot’s command prefix.

```bash
[p]load downloader
[p]repo add botagas-Cogs https://github.com/botagas/botagas-Cogs
[p]cog install botagas-Cogs <cogname>
[p]load <cogname>
```

---

## 🧩 Available Cogs

### 🏠 Roomer (fully reworked)
Roomer now supports **slash commands**, **interactive buttons**, **modals**, **select menus**, and **game presets** for managing temporary voice channels.

#### Slash Commands:
- `/roomer add <channel>` - Set `Join to Create` channel
- `/roomer remove <channel>` - Remove `Join to Create` channel
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
- After a restart or reload, you must re-run `/captcha deploy` to restore the verification button for proper operation

---

## 🙌 Credits

- **[Seina-Cogs](https://github.com/japandotorg/Seina-Cogs)** — for original Captcha logic
- **[Dav-Cogs](https://github.com/Dav-Git/Dav-Cogs)** — for original Roomer logic
- Thanks to the [Red Discord Bot](https://discord.gg/red) community for feedback and the Red Bot itself

---

## 💬 Need Help?

Join [`Red - Cog Support`](https://discord.gg/GET4DVk) for help or questions.  
I don’t have my own support channel, but you may find me there as **`@Winter`**. GitHub issue reports are welcome.
