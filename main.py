    if i.user.id!=i.guild.owner_id:return await respond(i,'Only the server owner can restore.')
    if confirmation!=f'RESTORE-{i.guild_id}':return await respond(i,f'To confirm, run again with confirmation: RESTORE-{i.guild_id}')
    await i.response.defer(ephemeral=True,thinking=True)
    row=await store.latest(i.guild_id)
    if not row:return await respond(i,'No backup available.')
    try:
        result=await restore_missing(i.guild,parse(row),f'Owner-requested recovery by {i.user.id}')
        await security.log(i.guild,i.user.id,'recovery',json.dumps(result)[:500]);await respond(i,'Recovery result: '+json.dumps(result,ensure_ascii=False)[:1200])
    except (discord.Forbidden,discord.HTTPException) as e:await respond(i,'Recovery incomplete: '+str(e))
@bot.tree.command(description='Temporarily lock text channels by denying send_messages to everyone')
@app_commands.guild_only()
@admin()
async def lockdown(i:discord.Interaction,confirmation:str):
    if confirmation!=f'LOCK-{i.guild_id}':return await respond(i,f'Confirm with LOCK-{i.guild_id}')
    await i.response.defer(ephemeral=True,thinking=True)
    count=0
    for c in i.guild.text_channels:
        try:
            await c.set_permissions(i.guild.default_role,send_messages=False,reason=f'Lockdown by {i.user.id}')
            count+=1
        except (discord.Forbidden,discord.HTTPException):pass
    await respond(i,f'Locked {count} text channels. IMPORTANT: original overwrites are not saved; unlock manually or restore from external permissions backup.')
@bot.tree.command(description='Ban member')
@app_commands.guild_only()
@app_commands.checks.has_permissions(ban_members=True)
async def ban(i:discord.Interaction,member:discord.Member,reason:str='No reason'):
    if member.id==i.guild.owner_id or member.top_role>=i.user.top_role or member.top_role>=i.guild.me.top_role:return await respond(i,'Role hierarchy prevents this action.')
    await member.ban(reason=f'{reason} | by {i.user.id}',delete_message_seconds=0);await respond(i,'Banned.')
@bot.tree.command(description='Kick member')
@app_commands.guild_only()
@app_commands.checks.has_permissions(kick_members=True)
async def kick(i:discord.Interaction,member:discord.Member,reason:str='No reason'):
    if member.id==i.guild.owner_id or member.top_role>=i.user.top_role or member.top_role>=i.guild.me.top_role:return await respond(i,'Role hierarchy prevents this action.')
    await member.kick(reason=f'{reason} | by {i.user.id}');await respond(i,'Kicked.')
@bot.tree.command(description='Timeout member in minutes')
@app_commands.guild_only()
@mod()
async def timeout(i:discord.Interaction,member:discord.Member,minutes:app_commands.Range[int,1,40320],reason:str='No reason'):
    if member.id==i.guild.owner_id or member.top_role>=i.user.top_role or member.top_role>=i.guild.me.top_role:return await respond(i,'Role hierarchy prevents this action.')
    await member.timeout(datetime.timedelta(minutes=minutes),reason=reason);await respond(i,'Timed out.')
@bot.tree.command(description='Delete up to 100 recent messages')
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_messages=True)
async def purge(i:discord.Interaction,count:app_commands.Range[int,1,100]):
    if not isinstance(i.channel,discord.TextChannel):return await respond(i,'Text channel only.')
    await i.response.defer(ephemeral=True,thinking=True)
    deleted=await i.channel.purge(limit=count,reason=f'Purge by {i.user.id}');await respond(i,f'Deleted {len(deleted)} messages.')
@bot.tree.command(description='Add a moderation warning')
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(i:discord.Interaction,member:discord.Member,reason:str):
    await store.pool.execute('INSERT INTO warnings(guild_id,user_id,mod_id,reason) VALUES($1,$2,$3,$4)',i.guild_id,member.id,i.user.id,reason)
    await respond(i,'Warning recorded.')
@bot.tree.command(description='List recent member warnings')
@app_commands.guild_only()
@mod()
async def warnings(i:discord.Interaction,member:discord.Member):
    rows=await store.pool.fetch('SELECT reason,created_at FROM warnings WHERE guild_id=$1 AND user_id=$2 ORDER BY id DESC LIMIT 10',i.guild_id,member.id)
    await respond(i,'\n'.join(f'{r["created_at"]:%Y-%m-%d}: {r["reason"][:120]}' for r in rows) or 'No warnings.')
@bot.tree.command(description='Recent security incidents')
@app_commands.guild_only()
@admin()
async def incidents(i:discord.Interaction):
    rows=await store.pool.fetch('SELECT kind,actor_id,created_at FROM incidents WHERE guild_id=$1 ORDER BY id DESC LIMIT 15',i.guild_id)
    await respond(i,'\n'.join(f'{r["created_at"]:%m-%d %H:%M} {r["kind"]} actor:{r["actor_id"]}' for r in rows) or 'No incidents.')
@bot.tree.command(description='Bot latency and uptime health')
@app_commands.guild_only()
async def ping(i:discord.Interaction):await respond(i,f'Pong {bot.latency*1000:.0f} ms')
@bot.tree.command(description='Link to dashboard')
@app_commands.guild_only()
async def dashboard(i:discord.Interaction):await respond(i,os.environ['DASHBOARD_URL'])

@bot.tree.error
async def on_error(i,error):
    if isinstance(error,app_commands.CommandInvokeError):
        logging.exception('Command error',exc_info=error.original)
        msg='Action failed; check bot permissions and logs.'
    elif isinstance(error,app_commands.MissingPermissions):msg='Insufficient permissions.'
    else:msg='Command unavailable or invalid.'
    try:await respond(i,msg)
    except discord.HTTPException:pass

@tasks.loop(minutes=5)
async def periodic_backup():
    interval=max(5,int(os.getenv('BACKUP_INTERVAL_MINUTES','30')))
    for g in bot.guilds:
        row=await store.latest(g.id)
        if row and (datetime.datetime.now(datetime.timezone.utc)-row['created_at']).total_seconds()<interval*60:continue
        try:await store.backup(g.id,await snapshot(g))
        except Exception:logging.exception('Backup failed for guild %s',g.id)
@periodic_backup.before_loop
async def before_backup():await bot.wait_until_ready()

async def run():
    await store.connect()
    init_message_restore, message_cleanup = install_message_restore(bot, store.pool)
    logging.info("message_restore registered: %s", bool(bot.tree.get_command("message_restore")))
    await init_message_restore()
    app = build_app(bot, store)
    if app is None:
        raise RuntimeError("Dashboard build_app() returned None")

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=int(os.getenv("PORT", "8080")),
            log_level="info",
            interface="asgi3"
        )
    )
    periodic_backup.start()
    message_cleanup.start()
    try:
        async with bot:
            # Global commands are synced in setup_hook after login.
            await asyncio.gather(bot.start(os.environ['DISCORD_TOKEN']),server.serve())
    finally:
        periodic_backup.cancel();message_cleanup.cancel();await store.close()
if __name__=='__main__':asyncio.run(run())
    if i.user.id!=i.guild.owner_id:return await respond(i,'Only the server owner can restore.')
    if confirmation!=f'RESTORE-{i.guild_id}':return await respond(i,f'To confirm, run again with confirmation: RESTORE-{i.guild_id}')
    await i.response.defer(ephemeral=True,thinking=True)
    row=await store.latest(i.guild_id)
    if not row:return await respond(i,'No backup available.')
    try:
        result=await restore_missing(i.guild,parse(row),f'Owner-requested recovery by {i.user.id}')
        await security.log(i.guild,i.user.id,'recovery',json.dumps(result)[:500]);await respond(i,'Recovery result: '+json.dumps(result,ensure_ascii=False)[:1200])
    except (discord.Forbidden,discord.HTTPException) as e:await respond(i,'Recovery incomplete: '+str(e))
@bot.tree.command(description='Temporarily lock text channels by denying send_messages to everyone')
@app_commands.guild_only()
@admin()
async def lockdown(i:discord.Interaction,confirmation:str):
    if confirmation!=f'LOCK-{i.guild_id}':return await respond(i,f'Confirm with LOCK-{i.guild_id}')
    await i.response.defer(ephemeral=True,thinking=True)
    count=0
    for c in i.guild.text_channels:
        try:
            await c.set_permissions(i.guild.default_role,send_messages=False,reason=f'Lockdown by {i.user.id}')
            count+=1
        except (discord.Forbidden,discord.HTTPException):pass
    await respond(i,f'Locked {count} text channels. IMPORTANT: original overwrites are not saved; unlock manually or restore from external permissions backup.')
@bot.tree.command(description='Ban member')
@app_commands.guild_only()
@app_commands.checks.has_permissions(ban_members=True)
async def ban(i:discord.Interaction,member:discord.Member,reason:str='No reason'):
    if member.id==i.guild.owner_id or member.top_role>=i.user.top_role or member.top_role>=i.guild.me.top_role:return await respond(i,'Role hierarchy prevents this action.')
    await member.ban(reason=f'{reason} | by {i.user.id}',delete_message_seconds=0);await respond(i,'Banned.')
@bot.tree.command(description='Kick member')
@app_commands.guild_only()
@app_commands.checks.has_permissions(kick_members=True)
async def kick(i:discord.Interaction,member:discord.Member,reason:str='No reason'):
    if member.id==i.guild.owner_id or member.top_role>=i.user.top_role or member.top_role>=i.guild.me.top_role:return await respond(i,'Role hierarchy prevents this action.')
    await member.kick(reason=f'{reason} | by {i.user.id}');await respond(i,'Kicked.')
@bot.tree.command(description='Timeout member in minutes')
@app_commands.guild_only()
@mod()
async def timeout(i:discord.Interaction,member:discord.Member,minutes:app_commands.Range[int,1,40320],reason:str='No reason'):
    if member.id==i.guild.owner_id or member.top_role>=i.user.top_role or member.top_role>=i.guild.me.top_role:return await respond(i,'Role hierarchy prevents this action.')
    await member.timeout(datetime.timedelta(minutes=minutes),reason=reason);await respond(i,'Timed out.')
@bot.tree.command(description='Delete up to 100 recent messages')
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_messages=True)
async def purge(i:discord.Interaction,count:app_commands.Range[int,1,100]):
    if not isinstance(i.channel,discord.TextChannel):return await respond(i,'Text channel only.')
    await i.response.defer(ephemeral=True,thinking=True)
    deleted=await i.channel.purge(limit=count,reason=f'Purge by {i.user.id}');await respond(i,f'Deleted {len(deleted)} messages.')
@bot.tree.command(description='Add a moderation warning')
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(i:discord.Interaction,member:discord.Member,reason:str):
    await store.pool.execute('INSERT INTO warnings(guild_id,user_id,mod_id,reason) VALUES($1,$2,$3,$4)',i.guild_id,member.id,i.user.id,reason)
    await respond(i,'Warning recorded.')
@bot.tree.command(description='List recent member warnings')
@app_commands.guild_only()
@mod()
async def warnings(i:discord.Interaction,member:discord.Member):
    rows=await store.pool.fetch('SELECT reason,created_at FROM warnings WHERE guild_id=$1 AND user_id=$2 ORDER BY id DESC LIMIT 10',i.guild_id,member.id)
    await respond(i,'\n'.join(f'{r["created_at"]:%Y-%m-%d}: {r["reason"][:120]}' for r in rows) or 'No warnings.')
@bot.tree.command(description='Recent security incidents')
@app_commands.guild_only()
@admin()
async def incidents(i:discord.Interaction):
    rows=await store.pool.fetch('SELECT kind,actor_id,created_at FROM incidents WHERE guild_id=$1 ORDER BY id DESC LIMIT 15',i.guild_id)
    await respond(i,'\n'.join(f'{r["created_at"]:%m-%d %H:%M} {r["kind"]} actor:{r["actor_id"]}' for r in rows) or 'No incidents.')
@bot.tree.command(description='Bot latency and uptime health')
@app_commands.guild_only()
async def ping(i:discord.Interaction):await respond(i,f'Pong {bot.latency*1000:.0f} ms')
@bot.tree.command(description='Link to dashboard')
@app_commands.guild_only()
async def dashboard(i:discord.Interaction):await respond(i,os.environ['DASHBOARD_URL'])

@bot.tree.error
async def on_error(i,error):
    if isinstance(error,app_commands.CommandInvokeError):
        logging.exception('Command error',exc_info=error.original)
        msg='Action failed; check bot permissions and logs.'
    elif isinstance(error,app_commands.MissingPermissions):msg='Insufficient permissions.'
    else:msg='Command unavailable or invalid.'
    try:await respond(i,msg)
    except discord.HTTPException:pass

@tasks.loop(minutes=5)
async def periodic_backup():
    interval=max(5,int(os.getenv('BACKUP_INTERVAL_MINUTES','30')))
    for g in bot.guilds:
        row=await store.latest(g.id)
        if row and (datetime.datetime.now(datetime.timezone.utc)-row['created_at']).total_seconds()<interval*60:continue
        try:await store.backup(g.id,await snapshot(g))
        except Exception:logging.exception('Backup failed for guild %s',g.id)
@periodic_backup.before_loop
async def before_backup():await bot.wait_until_ready()

async def run():
    await store.connect()
    init_message_restore, message_cleanup = install_message_restore(bot, store.pool)
    logging.info("message_restore registered: %s", bool(bot.tree.get_command("message_restore")))
    await init_message_restore()
    app = build_app(bot, store)
    if app is None:
        raise RuntimeError("Dashboard build_app() returned None")

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=int(os.getenv("PORT", "8080")),
            log_level="info",
            interface="asgi3"
        )
    )
    periodic_backup.start()
    message_cleanup.start()
    try:
        async with bot:
            # Global commands are synced in setup_hook after login.
            await asyncio.gather(bot.start(os.environ['DISCORD_TOKEN']),server.serve())
    finally:
        periodic_backup.cancel();message_cleanup.cancel();await store.close()
if __name__=='__main__':asyncio.run(run())
  try:
        async with bot:
            # Global commands are synced in setup_hook after login.
            await asyncio.gather(bot.start(os.environ['DISCORD_TOKEN']),server.serve())
    finally:
        periodic_backup.cancel();message_cleanup.cancel();await store.close()
if __name__=='__main__':asyncio.run(run())
