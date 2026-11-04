ALL4U - PERSISTENT MESSAGE ARCHIVE

Files: main.py (existing bot), message_restore.py (replacement module).
Keep the other project files (store.py, security.py, recovery.py, dashboard.py, requirements.txt) unchanged.

1. Replace main.py and message_restore.py in your existing GitHub repository.
2. Railway: connect the bot service to a persistent PostgreSQL service and set DATABASE_URL to its internal connection string.
3. In Discord Developer Portal > Bot, enable MESSAGE CONTENT INTENT. Railway Variables: ENABLE_MESSAGE_CONTENT=true.
4. Ensure the bot has View Channel, Read Message History, Send Messages permissions.
5. Redeploy and check logs for "Persistent message archive initialized".
6. Use /message_backup_status, /message_backup, /message_restore.

There is no time-based deletion. Storage capacity, database availability, and any provider retention policies still apply.
Attachments store metadata/URLs, not downloaded file bytes. URLs may expire.
Old records already deleted by the previous 24h cleanup cannot be recovered.
This package is based on a previously saved All4u main.py, not verified against the currently deployed GitHub commit.
