import os, asyncio, datetime, json, logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
import uvicorn
from store import Store
from security import Security
from recovery import snapshot,parse,preview,restore_missing
from dashboard import build_app
from message_restore import install as install_message_restore

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
for k in ('DISCORD_TOKEN','DISCORD_CLIENT_ID','DISCORD_CLIENT_SECRET','DASHBOARD_URL','SESSION_SECRET','DATABASE_URL'):
    if not os.getenv(k):raise RuntimeError('Missing environment variable: '+k)
intents=discord.Intents.default();intents.members=True
intents.message_content=os.getenv('ENABLE_MESSAGE_CONTENT','true').lower()=='true'
class SecurityBot(commands.Bot):
    async def setup_hook(self):
        await self.tree.sync()

bot=SecurityBot(command_prefix=commands.when_mentioned, intents=intents,help_command=None)
store=Store();security=Security(bot,store)

def admin():return app_commands.checks.has_permissions(administrator=True)
def mod():return app_commands.checks.has_permissions(moderate_members=True)
async def respond(interaction,text,ephemeral=True):
    if interaction.response.is_done():await interaction.followup.send(text[:1900],ephemeral=ephemeral)
    else:await interaction.response.send_message(text[:1900],ephemeral=ephemeral)

@bot.event
async def on_ready():logging.info('Ready as %s | %s guilds',bot.user,len(bot.guilds))
@bot.event
async def on_guild_channel_delete(channel):await security.audit_event(channel.guild,discord.AuditLogAction.channel_delete,channel.id,'channel_delete')
@bot.event
async def on_guild_role_delete(role):await security.audit_event(role.guild,discord.AuditLogAction.role_delete,role.id,'role_delete')
@bot.event
async def on_guild_role_update(before,after):
    if before.permissions!=after.permissions:await security.audit_event(after.guild,discord.AuditLogAction.role_update,after.id,'role_permission_change',2)
@bot.event
async def on_guild_channel_update(before,after):
    if before.overwrites!=after.overwrites:await security.audit_event(after.guild,discord.AuditLogAction.channel_update,after.id,'channel_permission_change',2)
@bot.event
async def on_member_ban(guild,user):await security.audit_event(guild,discord.AuditLogAction.ban,user.id,'mass_ban',3)
@bot.event
async def on_member_remove(member):
    # Audit logs distinguish kicks from voluntary leaves; skip if not reliably attributable.
    actor=await security.audit_actor(member.guild,discord.AuditLogAction.kick,member.id)
    if actor:
        n=security.count((member.guild.id,actor.id,'mass_kick'),30)
        await security.log(member.guild,actor.id,'kick',f'target={member.id}, count={n}')
        if n>=3:await security.punish(member.guild,actor,'mass_kick')
@bot.event
async def on_webhooks_update(channel):
    await security.log(channel.guild,0,'webhook_changed',f'channel={channel.id}; inspect audit logs')
@bot.event
async def on_member_join(member):await security.join(member)
@bot.event
async def on_message(message):await security.message(message)

@bot.tree.command(description='Security overview')
@app_commands.guild_only()
@admin()
async def security_status(i:discord.Interaction):
    cfg=await store.config(i.guild_id)
    await respond(i,'🛡️ Security: '+', '.join(f'{x}={cfg.get(x,True)}' for x in ('antinuke','antiraid','antispam')))
@bot.tree.command(description='Configure security protection')
@app_commands.guild_only()
@admin()
@app_commands.choices(feature=[app_commands.Choice(name=x,value=x) for x in ('antinuke','antiraid','antispam')])
async def security_set(i:discord.Interaction,feature:app_commands.Choice[str],enabled:bool):
    cfg=await store.config(i.guild_id);cfg[feature.value]=enabled;await store.put(i.guild_id,cfg)
    await security.log(i.guild,i.user.id,'config',f'{feature.value}={enabled}')
    await respond(i,f'{feature.value}: {enabled}')
@bot.tree.command(description='Choose a security logging channel')
@app_commands.guild_only()
@admin()
async def security_logs(i:discord.Interaction,channel:discord.TextChannel):
    cfg=await store.config(i.guild_id);cfg['log_channel_id']=channel.id;await store.put(i.guild_id,cfg)
    await respond(i,'Logging channel: '+channel.mention)
@bot.tree.command(description='Trust an administrator (server owner only)')
@app_commands.guild_only()
async def trusted_add(i:discord.Interaction,member:discord.Member):
    if i.user.id!=i.guild.owner_id:return await respond(i,'Only the server owner can change trust.')
    cfg=await store.config(i.guild_id);ids=set(cfg.get('trusted',[]));ids.add(member.id);cfg['trusted']=sorted(ids);await store.put(i.guild_id,cfg)
    await respond(i,f'Trusted: {member.mention}')
@bot.tree.command(description='Remove a trusted user (server owner only)')
@app_commands.guild_only()
async def trusted_remove(i:discord.Interaction,member:discord.Member):
    if i.user.id!=i.guild.owner_id:return await respond(i,'Only the server owner can change trust.')
    cfg=await store.config(i.guild_id);cfg['trusted']=[x for x in cfg.get('trusted',[]) if x!=member.id];await store.put(i.guild_id,cfg)
    await respond(i,'Removed from trusted list.')
@bot.tree.command(description='Create server structure backup')
@app_commands.guild_only()
@admin()
async def backup_create(i:discord.Interaction):
    await i.response.defer(ephemeral=True,thinking=True)
