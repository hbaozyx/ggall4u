"""Optional 24-hour deleted-message archive for All4u."""
import asyncio
import datetime as dt
import logging
import discord
from discord import app_commands
from discord.ext import tasks

RETENTION = dt.timedelta(hours=24)


def install(bot, pool):
    async def init():
        await pool.execute('''CREATE TABLE IF NOT EXISTS message_restore_archive (
            guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
            message_id BIGINT PRIMARY KEY, author_id BIGINT NOT NULL,
            content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL,
            deleted_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ NOT NULL
        )''')
        await pool.execute('CREATE INDEX IF NOT EXISTS idx_message_restore_expiry ON message_restore_archive(expires_at)')
        await pool.execute('DELETE FROM message_restore_archive WHERE expires_at <= NOW()')

    async def archive(message):
        if not message.guild or message.author.bot or not message.content:
            return
        # No DMs, attachments, or private threads are archived.
        if isinstance(message.channel, discord.Thread):
            return
        try:
            await pool.execute('''INSERT INTO message_restore_archive
                (guild_id,channel_id,message_id,author_id,content,created_at,expires_at)
                VALUES ($1,$2,$3,$4,$5,$6,NOW() + INTERVAL '24 hours')
                ON CONFLICT(message_id) DO UPDATE SET content=EXCLUDED.content
                WHERE message_restore_archive.deleted_at IS NULL''',
                message.guild.id,message.channel.id,message.id,message.author.id,
                message.content[:4000],message.created_at)
        except Exception:
            logging.exception('Message archive failed')

    async def deleted(payload):
        if payload.guild_id:
            try:
                await pool.execute('''UPDATE message_restore_archive SET deleted_at=NOW()
                    WHERE message_id=$1 AND guild_id=$2 AND deleted_at IS NULL''',payload.message_id,payload.guild_id)
            except Exception:
                logging.exception('Message deletion tracking failed')

    @tasks.loop(minutes=10)
    async def cleanup():
        try:
            await pool.execute('DELETE FROM message_restore_archive WHERE expires_at <= NOW()')
        except Exception:
            logging.exception('Message archive cleanup failed')

    @cleanup.before_loop
    async def before_cleanup():
        await bot.wait_until_ready()

    @app_commands.command(name='message_restore',description='Restore one archived deleted message (admin only)')
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(message_id='ID of the deleted message', publish='Post restored text to the original channel')
    async def message_restore(interaction:discord.Interaction,message_id:str,publish:bool=False):
        await interaction.response.defer(ephemeral=True)
        if not message_id.isdecimal() or len(message_id)>20:
            return await interaction.followup.send('ID tin nhắn không hợp lệ.',ephemeral=True)
        row=await pool.fetchrow('''SELECT * FROM message_restore_archive
            WHERE guild_id=$1 AND message_id=$2 AND deleted_at IS NOT NULL
            AND expires_at>NOW()''',interaction.guild_id,int(message_id))
        if not row:
            return await interaction.followup.send('Không có bản sao tin nhắn đã xóa trong 24 giờ.',ephemeral=True)
        channel=interaction.guild.get_channel(row['channel_id'])
        if not isinstance(channel,discord.TextChannel):
            return await interaction.followup.send('Kênh gốc không còn khả dụng.',ephemeral=True)
        me=interaction.guild.me
        if not channel.permissions_for(me).send_messages:
            return await interaction.followup.send('Bot không có quyền gửi tin nhắn tại kênh gốc.',ephemeral=True)
        content=row['content']
        if not publish:
            preview=f"Tác giả: {row['author_id']} | Kênh: {channel.mention}\nNội dung:\n{content[:1500]}"
            return await interaction.followup.send(preview,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        header=f"[All4u khôi phục] Tin nhắn cũ của user ID {row['author_id']} (ID: {row['message_id']}). Nội dung bản sao:\n"
        try:
            for start in range(0,len(content),1700):
                part=content[start:start+1700]
                await channel.send((header if start==0 else '[Tiếp nội dung khôi phục]\n')+part,
                                   allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            return await interaction.followup.send('Không thể đăng bản sao vào kênh gốc.',ephemeral=True)
        await interaction.followup.send('Đã đăng lại bản sao vào kênh gốc.',ephemeral=True)

    bot.add_listener(archive,'on_message')
    bot.add_listener(deleted,'on_raw_message_delete')
    bot.tree.add_command(message_restore)
    return init, cleanup
