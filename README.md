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

### 🏠 Roomer (reworked)
Roomer now fully supports **slash commands** via `/roomer`.

#### Supported Slash Commands:
- `/roomer add <channel>`
- `/roomer enable`
- `/roomer disable`

#### New Features:
- Sends an embed to the room channel with the following **modals**:
  - **Lock / Unlock** — Toggle access for `@everyone`
  - **Rename** — Set a custom name for the channel
  - **Set Limit** — Define max users in the room
  - **Claim Room** — Take over ownership if the original creator has left
- Room settings are only changeable by the channel creator or current owner

---

### 🧪 Captcha (reworked)
The new Captcha system uses **modals and interactions** instead of classic text command responses. No more clutter from join messages and replies.

#### Supported Slash Commands:
- `/captcha channel <channel>` — Set the verification channel
- `/captcha toggle` — Enable/disable captcha verification
- `/captcha deploy` — Deploy the verification embed
- `/captcha role <VerifiedRole>` — Assign verified role
- `/captcha unverifiedrole <UnverifiedRole>` — Assign unverified role
- `/captcha tries <number>` — Set max attempts
- `/captcha timeout <number>` — Set timeout before invalidating verification attempt
- `/captcha embed <text>` — Set embed message
- `/captcha before <text>` — Message shown before verification
- `/captcha after <text>` — Message shown after success

#### Features:
- Deploys a **persistent embed** with a verification button
- Two verification methods:
  - _If DMs are enabled_: user gets a DM with an image captcha
  - _If DMs are disabled_: fallback to a **modal** with code input
- Insteaf of kicking the user on timeout, now the verification attempt is invalidated and the user can try again.
- **Automatic cleanup**:
  - Captcha messages in DMs are auto-deleted after verification
  - Server messages are cleaned up via async background task

#### Known Limitations:
- ⚠️ After a bot restart or cog reload, you must re-run `/captcha deploy` to re-send the verification message. The old message is deleted automatically.

---

## 🙌 Credits

- **[Seina-Cogs](https://github.com/japandotorg/Seina-Cogs)** — for the original Captcha logic
- **[Dav-Cogs](https://github.com/Dav-Git/Dav-Cogs)** — for Roomer base
- Thanks to the [Red Discord Bot](https://discord.gg/red) community for continuous support

---

## 💬 Need Help?

Join the official [`Red - Cog Support`](https://discord.gg/GET4DVk) server for help or questions. I do not have a support channel, but I am present as **`@Winter`**.
