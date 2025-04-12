import discord


def format_message(template: str, member: discord.Member) -> str:
    return template.format(
        mention=member.mention,
        name=member.display_name,
        username=member.name,
        guild=member.guild.name,
        id=member.id,
    )
