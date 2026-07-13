from typing import Any, Dict, List, Optional

import discord


class DetailsModal(discord.ui.Modal, title="Announcement Details"):
    game_name = discord.ui.TextInput(label="Game name", max_length=100, required=True)
    party = discord.ui.TextInput(
        label="Game party size/capacity",
        placeholder="For example: 2/4 or 4",
        max_length=32,
        required=False,
    )
    description = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph, max_length=1000, required=False
    )
    image_url = discord.ui.TextInput(label="HTTPS image URL", max_length=1000, required=False)
    note = discord.ui.TextInput(
        label="Custom note", style=discord.TextStyle.paragraph, max_length=500, required=False
    )

    def __init__(self, cog: Any, channel_id: int, resolved: Optional[Dict[str, Any]] = None):
        super().__init__(timeout=300)
        self.cog = cog
        self.channel_id = channel_id
        resolved = resolved or {}
        self.game_name.default = resolved.get("game_name") or None
        self.party.default = resolved.get("party") or None
        self.description.default = resolved.get("description") or None
        self.image_url.default = resolved.get("image_url") or None
        self.note.default = resolved.get("note") or None

    async def on_submit(self, interaction: discord.Interaction):
        image_url = self.image_url.value.strip()
        if image_url and not image_url.startswith("https://"):
            return await interaction.response.send_message(
                "❌ Image URLs must start with `https://`.", ephemeral=True
            )
        await self.cog.apply_manual_details(
            interaction,
            self.channel_id,
            {
                "game_name": self.game_name.value.strip(),
                "party": self.party.value.strip(),
                "description": self.description.value.strip(),
                "image_url": image_url,
                "note": self.note.value.strip(),
            },
        )


class GameSelect(discord.ui.Select):
    def __init__(self, cog: Any, channel_id: int, options: List[discord.SelectOption]):
        super().__init__(
            placeholder="Choose a preset, detected game, or manual entry",
            options=options[:25],
            min_values=1,
            max_values=1,
        )
        self.cog = cog
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == "manual":
            return await interaction.response.send_modal(DetailsModal(self.cog, self.channel_id))
        await self.cog.choose_game_source(interaction, self.channel_id, selected)


class GameChoiceView(discord.ui.View):
    def __init__(self, cog: Any, channel_id: int, options: List[discord.SelectOption]):
        super().__init__(timeout=180)
        self.add_item(GameSelect(cog, channel_id, options))


class RoleSelect(discord.ui.Select):
    def __init__(self, cog: Any, channel_id: int, roles: List[discord.Role]):
        options = [discord.SelectOption(label="No role", value="none")]
        options.extend(
            discord.SelectOption(label=role.name[:100], value=str(role.id)) for role in roles[:24]
        )
        super().__init__(
            placeholder="Choose an approved announcement role",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.cog = cog
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        await self.cog.select_role(
            interaction, self.channel_id, None if value == "none" else int(value)
        )


class RoleChoiceView(discord.ui.View):
    def __init__(self, cog: Any, channel_id: int, roles: List[discord.Role]):
        super().__init__(timeout=180)
        self.add_item(RoleSelect(cog, channel_id, roles))


class MetadataSelect(discord.ui.Select):
    def __init__(self, cog: Any, channel_id: int, candidates: List[Dict[str, Any]]):
        options = [
            discord.SelectOption(
                label=item.get("name", "Unknown")[:100],
                value=str(index),
                description=(item.get("description") or "No description")[:100],
            )
            for index, item in enumerate(candidates[:25])
        ]
        super().__init__(placeholder="Select the matching game", options=options)
        self.cog = cog
        self.channel_id = channel_id
        self.candidates = candidates

    async def callback(self, interaction: discord.Interaction):
        await self.cog.select_metadata_candidate(
            interaction, self.channel_id, self.candidates[int(self.values[0])]
        )


class MetadataChoiceView(discord.ui.View):
    def __init__(self, cog: Any, channel_id: int, candidates: List[Dict[str, Any]]):
        super().__init__(timeout=180)
        self.add_item(MetadataSelect(cog, channel_id, candidates))


class RSVPView(discord.ui.View):
    def __init__(self, cog: Any, channel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.bot:
            await interaction.response.send_message("Bots cannot RSVP.", ephemeral=True)
            return False
        return True

    @discord.ui.button(
        label="Join",
        custom_id="roomannounce:rsvp:join",
        style=discord.ButtonStyle.success,
    )
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.set_rsvp(interaction, self.channel_id, "join")

    @discord.ui.button(
        label="Maybe",
        custom_id="roomannounce:rsvp:maybe",
        style=discord.ButtonStyle.primary,
    )
    async def maybe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.set_rsvp(interaction, self.channel_id, "maybe")

    @discord.ui.button(
        label="Not Coming",
        custom_id="roomannounce:rsvp:not_coming",
        style=discord.ButtonStyle.danger,
    )
    async def not_coming(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.set_rsvp(interaction, self.channel_id, "not_coming")

    @discord.ui.button(
        label="Participants",
        custom_id="roomannounce:rsvp:participants",
        style=discord.ButtonStyle.secondary,
    )
    async def participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_participants(interaction, self.channel_id)


class AnnouncementControlView(discord.ui.View):
    def __init__(
        self,
        cog: Any,
        channel_id: int,
        announcements_enabled: bool = True,
        preview_visible: bool = False,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        for item in self.children:
            if item.custom_id == "roomannounce:toggle_enabled":
                item.label = (
                    "Disable Announcements" if announcements_enabled else "Enable Announcements"
                )
                item.style = (
                    discord.ButtonStyle.danger
                    if announcements_enabled
                    else discord.ButtonStyle.success
                )
            elif item.custom_id == "roomannounce:toggle_preview":
                item.label = "Hide Preview" if preview_visible else "Show Preview"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.cog.check_owner(interaction, self.channel_id)

    @discord.ui.button(
        label="Announce / Update",
        row=0,
        custom_id="roomannounce:publish",
        style=discord.ButtonStyle.success,
    )
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.publish_from_interaction(interaction, self.channel_id)

    @discord.ui.button(
        label="Edit Details",
        row=0,
        custom_id="roomannounce:edit",
        style=discord.ButtonStyle.primary,
    )
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = await self.cog.get_state(self.channel_id)
        await interaction.response.send_modal(
            DetailsModal(self.cog, self.channel_id, state.get("resolved"))
        )

    @discord.ui.button(
        label="Choose Game / Preset",
        row=0,
        custom_id="roomannounce:game",
        style=discord.ButtonStyle.primary,
    )
    async def game(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_game_choices(interaction, self.channel_id)

    @discord.ui.button(
        label="Select / Tag Role",
        row=0,
        custom_id="roomannounce:role",
        style=discord.ButtonStyle.secondary,
    )
    async def role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_role_choices(interaction, self.channel_id)

    @discord.ui.button(
        label="Refresh Data",
        row=0,
        custom_id="roomannounce:refresh",
        style=discord.ButtonStyle.secondary,
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.refresh_from_interaction(interaction, self.channel_id)

    @discord.ui.button(
        label="Disable Announcements",
        row=1,
        custom_id="roomannounce:toggle_enabled",
        style=discord.ButtonStyle.danger,
    )
    async def toggle_enabled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.toggle_announcements(interaction, self.channel_id)

    @discord.ui.button(
        label="Show Preview",
        row=1,
        custom_id="roomannounce:toggle_preview",
        style=discord.ButtonStyle.secondary,
    )
    async def toggle_preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.toggle_preview(interaction, self.channel_id)

    @discord.ui.button(
        label="Delete Public",
        row=1,
        custom_id="roomannounce:delete_public",
        style=discord.ButtonStyle.danger,
    )
    async def delete_public(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.delete_public_from_interaction(interaction, self.channel_id)

    @discord.ui.button(
        label="Reset Overrides",
        row=1,
        custom_id="roomannounce:reset",
        style=discord.ButtonStyle.secondary,
    )
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.reset_overrides(interaction, self.channel_id)
