ALL4U - Message restore patch (3 Python files)

Replace these three files in the ROOT of your GitHub repository:
- main.py
- message_restore.py
- store.py

Keep your existing security.py, recovery.py, dashboard.py, requirements.txt and other project files.
Commit to GitHub and confirm Railway deploys the NEW commit.
Expected startup log: Local slash commands (22), Global slash commands synced (22).
Commands: /message_restore, /message_backup_status, /message_backup, /message_deleted_list.

Message archive does not automatically expire; PostgreSQL retention still depends on your database service and storage.
Already-deleted messages that were never captured cannot be recovered.
Ensure Message Content Intent is enabled in Discord Developer Portal and ENABLE_MESSAGE_CONTENT=true in Railway.
