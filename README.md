# botagas-Cogs V3

[![CodeQL](https://github.com/botagas/botagas-Cogs/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/botagas/botagas-Cogs/blob/main/.github/workflows/codeql-analysis.yml) [![Linting](https://github.com/botagas/botagas-Cogs/actions/workflows/tests.yml/badge.svg)](https://github.com/botagas/botagas-Cogs/blob/main/.github/workflows/tests.yml) 

bitasid's Unapproved Cogs for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot/). Initially forked from [Seina-Cogs](https://github.com/japandotorg/Seina-Cogs) for Captcha rework, later expanded with Roomer from [Dav-Cogs](https://github.com/Dav-Git/Dav-Cogs) by implementing slash-command support and implementing modals for channel management instead of commands. Credit goes to the original authors for their efforts and code to be reworked, improved and built upon.

## Installation
> Substitute `[p]` for your prefix.
1. Load downloader by using `[p]load downloader`
2. Add `botagas-Cogs` to your Red instance by using `[p]repo add botagas-Cogs https://github.com/botagas/botagas-Cogs`
3. Install cog(s) from `botagas-Cogs` in your Red instance by using `[p]cog install botagas-Cogs <CogName>`
4. Load the cog(s) by using `[p]load <cogName>`

## Documentation
### Roomer rework
Roomer now supports slash commands with `/roomer`.
Possible arguments are: `add <channel>`, `enable`, `disable`.
Redundant (to be removed): `limit`, `name`, `remove`.

It also has these new features:
- A management embed with buttons is sent to the newly created voice channel. Currently supported modals are:
  - **Lock** - lock the room for the @everyone role;
  - **Unlock** - unlock the room for the @everyone role;
  - **Rename** - change the title of the room;
  - **Set Limit** - change the max amount of users that can be present in the room;
  - **Claim Room** - become the manager / owner of the room (channel). 
- Room features are only change-able by the creator of the channel.
- If the creator is not present (has left), any other user inside or outside of the room can claim the room for themselves to manage it.

### Captcha rework
Captcha now uses modals and interactions instead of sending messages on each user's join and waiting for their reply which generates unnecessary message notifications.
Current implementation does not YET support slash commands. 
## Credits
- Thanks to the community in the [Red Server](https://discord.gg/red) for the resources and to the developers for creating the bot.
- Thanks to Seina-Cogs for their Captcha cog. Visit their repository at [Seina-Cogs](https://github.com/japandotorg/Seina-Cogs) here.
- Thanks to Dav-Cogs for their Roomer cog. Visit their repository at [Dav-Cogs](https://github.com/Dav-Git/Dav-Cogs) here.

For support regarding Red Bot cogs, join the official [`Red - Cog Support`](https://discord.gg/GET4DVk) server. 
