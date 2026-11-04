"""Persistent message archive for All4u; PostgreSQL is the source of truth."""
import json
import logging
import discord
from discord import app_commands
from discord.ext import tasks


def install(bot, pool):
    async def init():
        await pool.execute('''CREATE TABLE IF NOT EXISTS message_restore_archive (
            guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
            message_id BIGINT PRIMARY KEY, author_id BIGINT NOT NULL,
            content TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL,
            deleted_at TIMESTAMPTZ, expires_at TIMESTAMPTZ
        )''')
        # Keep compatibility with existing 24-hour archive; never delete old rows.
        await pool.execute('ALTER TABLE message_restore_archive ALTER COLUMN expires_at DROP NOT NULL')
        await pool.execute('ALTER TABLE message_restore_archive ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT \'[]\'::jsonb')
        await pool.execute('CREATE INDEX IF NOT EXISTS msg_archive_guild_deleted ON message_restore_archive(guild_id,deleted_at DESC)')
        await pool.execute('CREATE INDEX IF NOT EXISTS msg_archive_channel ON message_restore_archive(guild_id,channel_id,created_at DESC)')
        await pool.execute('ALTER TABLE message_restore_archive DROP COLUMN IF EXISTS expires_at')
        logging.info('Persistent message archive initialized; automatic expiration disabled')

    async def save(message):
        if not message.guild or message.author.bot or isinstance(message.channel, discord.Thread):
            return
        attachments = [{'name': a.filename, 'url': a.url, 'size': a.size} for a in message.attachments]
        try:
            await pool.execute('''INSERT INTO message_restore_archive
                (guild_id,channel_id,message_id,author_id,content,created_at,attachments)
                VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
                ON CONFLICT(message_id) DO UPDATE SET content=EXCLUDED.content,
                attachments=EXCLUDED.attachments WHERE message_restore_archive.deleted_at IS NULL''',
                message.guild.id,message.channel.id,message.id,message.author.id,
                message.content,message.created_at,json.dumps(attachments))
        except Exception:
            logging.exception('Message backup failed: %s', message.id)

    async def deleted(payload):
        if not payload.guild_id: return
        try:
            await pool.execute('''UPDATE message_restore_archive SET deleted_at=COALESCE(deleted_at,NOW())
                WHERE guild_id=$1 AND message_id=$2''',payload.guild_id,payload.message_id)
        except Exception: logging.exception('Message deletion tracking failed')

    async def bulk_deleted(payload):
        if not payload.guild_id or not payload.message_ids: return
        try:
            await pool.execute('''UPDATE message_restore_archive SET deleted_at=COALESCE(deleted_at,NOW())
                WHERE guild_id=$1 AND message_id=ANY($2::bigint[])''',payload.guild_id,list(payload.message_ids))
        except Exception: logging.exception('Bulk deletion tracking failed')

    @tasks.loop(hours=24)
    async def cleanup():
        # Retained for backwards compatibility with main.py; no data is deleted.
        logging.info('Archive retention: unlimited (no expiry cleanup)')

    @cleanup.before_loop
    async def before_cleanup(): await bot.wait_until_ready()

    async def send_private(i, message):
        await i.followup.send(message[:1900],ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name='message_restore',description='Preview or repost a saved deleted message')
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(message_id='Deleted Discord message ID',publish='Repost to original channel (default: private preview)')
    async def message_restore(i:discord.Interaction,message_id:str,publish:bool=False):
        await i.response.defer(ephemeral=True)
        if not message_id.isdecimal() or int(message_id)>9223372036854775807:
            return await send_private(i,'ID tin nhắn không hợp lệ.')
        row=await pool.fetchrow('''SELECT * FROM message_restore_archive
            WHERE guild_id=$1 AND message_id=$2 AND deleted_at IS NOT NULL''',i.guild_id,int(message_id))
        if not row:
            return await send_private(i,'Không tìm thấy tin nhắn đã xóa trong bản sao lưu. Kiểm tra ID, Message Content Intent và dữ liệu PostgreSQL.')
        content=row['content'] or '[Không có nội dung văn bản]'
        files=row['attachments']
        if isinstance(files,str): files=json.loads(files)
        attachment_lines='\n'.join(f"{a.get('name','file')}: {a.get('url','')}" for a in files)[:1000]
        text=f"[All4u | Bản sao tin nhắn đã xóa]\nTác giả ID: {row['author_id']} | Tin nhắn ID: {row['message_id']}\nNội dung:\n{content}"
        if attachment_lines: text+='\nTệp đính kèm (URL có thể hết hạn):\n'+attachment_lines
        if not publish:
            return await send_private(i,text)
        channel=i.guild.get_channel(row['channel_id'])
        if channel is None:
            try: channel=await i.guild.fetch_channel(row['channel_id'])
            except discord.HTTPException: channel=None
        if not isinstance(channel,discord.TextChannel):
            return await send_private(i,'Kênh gốc không tồn tại hoặc không phải kênh văn bản.')
        if not channel.permissions_for(i.guild.me).send_messages:
            return await send_private(i,'Bot thiếu quyền Send Messages trong kênh gốc.')
        try:
            for start in range(0,len(text),1900):
                await channel.send(text[start:start+1900],allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            return await send_private(i,'Không thể đăng bản sao. Kiểm tra quyền bot và Railway Logs.')
        await send_private(i,'Đã đăng lại bản sao trong kênh gốc.')

    @app_commands.command(name='message_backup_status',description='Check persistent message archive statistics')
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def message_backup_status(i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        row=await pool.fetchrow('''SELECT COUNT(*) AS total,COUNT(*) FILTER(WHERE deleted_at IS NOT NULL) AS deleted,
            MIN(created_at) AS oldest FROM message_restore_archive WHERE guild_id=$1''',i.guild_id)
        await send_private(i,f"Bản sao lưu PostgreSQL: {row['total']} tin nhắn | {row['deleted']} đã xóa | Cũ nhất: {row['oldest'] or 'chưa có'} | Không tự động hết hạn.")

    @app_commands.command(name='message_backup',description='Backfill recent messages from a text channel')
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel='Channel to back up',limit='Recent messages to read (1–1000)')
    async def message_backup(i:discord.Interaction,channel:discord.TextChannel,limit:app_commands.Range[int,1,1000]=100):
        await i.response.defer(ephemeral=True)
        if not channel.permissions_for(i.guild.me).read_message_history:
            return await send_private(i,'Bot thiếu quyền Read Message History.')
        count=0
        try:
            async for message in channel.history(limit=limit,oldest_first=True):
                if not message.author.bot:
                    await save(message)
                    count+=1
        except discord.HTTPException:
            return await send_private(i,'Không thể đọc lịch sử kênh. Kiểm tra quyền View Channel và Read Message History.')
        await send_private(i,f'Đã xử lý {count} tin nhắn gần đây từ {channel.mention}.')

    @app_commands.command(name='message_deleted_list',description='List the latest archived deleted messages')
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def message_deleted_list(i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        rows=await pool.fetch("""SELECT message_id,channel_id,deleted_at FROM message_restore_archive
            WHERE guild_id=$1 AND deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT 15""",i.guild_id)
        if not rows:
            return await send_private(i,'Chưa có tin nhắn đã xóa được lưu trong PostgreSQL. Hãy kiểm tra /message_backup_status.')
        lines=[f"ID: {r['message_id']} | Kênh: <#{r['channel_id']}> | Xóa: {r['deleted_at']:%Y-%m-%d %H:%M UTC}" for r in rows]
        await send_private(i,'15 tin nhắn đã xóa gần nhất trong bản sao lưu:\n'+'\n'.join(lines))

    bot.add_listener(save,'on_message')
    async def edited(before,after): await save(after)
    bot.add_listener(edited,'on_message_edit')
    bot.add_listener(deleted,'on_raw_message_delete')
    bot.add_listener(bulk_deleted,'on_raw_bulk_message_delete')
    for command in (message_restore,message_backup_status,message_backup,message_deleted_list): bot.tree.add_command(command)
    return init,cleanup
