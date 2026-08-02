# Active Context

**Дата фокуса:** 02.08.2026 (UTC+3)  
**Ветки дня:** 02.08.26-PRD-BOT-ALL / 02.08.26-AGENT-WORLD (disk cleanup)  
**Предыдущий tip:** PRD 71bd4ba · AW f6c4d8c

## Текущий фокус

1. Disk cleanup: scripts/server_disk_cleanup.sh + prune_config_backups.sh (keep 1 bak).
2. Install/deploy после bak — prune старых копий.
3. ПК: scripts/pc_bot_folders_cleanup.ps1 (__pycache__, старые bak, Temp clones).
4. SSH OK: IP 207.154.238.178.

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| User | root |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |

## Не смешивать

- /root/PRD-BOT-ALL ← только *-PRD-BOT-ALL
- /root/AGENT-WORLD ← только *-AGENT-WORLD
