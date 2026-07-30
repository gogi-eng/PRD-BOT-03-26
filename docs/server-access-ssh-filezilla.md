# Доступ к серверу DigitalOcean (SSH и FileZilla)

**Для кого:** восстановление входа на VPS, когда «пароль не тот» или FileZilla не подключается.  
**Репозиторий:** [gogi-eng/PRD-BOT-03-26](https://github.com/gogi-eng/PRD-BOT-03-26)  
**Архив сессии 30.07.2026:** [`.cursor/chats/archive/30.07.26-ssh-filezilla-access.md`](../.cursor/chats/archive/30.07.26-ssh-filezilla-access.md)

> **Безопасность.** В git **нельзя** класть: пароль root, файл `id_rsa` (без `.pub`), `.env`, API-ключи.  
> Публичный ключ берите **только с ПК**: `C:\Users\<Имя>\.ssh\id_rsa.pub` (не вставляйте полный ключ в публичный репозиторий без необходимости).

---

## Актуальный адрес сервера

| Параметр | Значение (на 30.07.2026) |
|----------|---------------------------|
| IP | `207.154.238.178` |
| Пользователь | `root` |
| Порт SSH/SFTP | `22` |
| Путь прод | `/root/PRD-BOT-ALL` |
| Путь песочница | `/root/AGENT-WORLD` |

**Всегда проверяйте IP в DigitalOcean:** Control Panel → Droplets → ваш дроплет → IP.  
Старый адрес `164.90.168.39` может быть **устаревшим** (после смены/пересоздания дроплета).

---

## 1) SSH с Windows (предпочтительно — ключ, не пароль)

На ПК обычно есть:

- приватный ключ: `C:\Users\<Имя>\.ssh\id_rsa` — **никому не отправлять, в git не класть**
- публичный ключ: `C:\Users\<Имя>\.ssh\id_rsa.pub` — его можно один раз добавить на сервер

Файл `C:\Users\<Имя>\.ssh\config` (пример, IP подставьте из DigitalOcean):

```text
Host prd-bot-do
    HostName 207.154.238.178
    User root
    IdentityFile ~/.ssh/id_rsa
    IdentitiesOnly yes
```

Проверка с ПК (без пароля, только ключ):

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=10 root@207.154.238.178 "hostname; ls -d /root/PRD-BOT-ALL /root/AGENT-WORLD"
```

Или коротко: `ssh prd-bot-do`.

| Результат | Что значит |
|-----------|------------|
| Показались `hostname` и папки | Доступ по ключу **есть** |
| `Permission denied` | Ключ не в `authorized_keys` на сервере (см. §3) |
| `Connection timed out` | Неверный IP / файрвол / дроплет выключен — сверьте IP в DO |

---

## 2) FileZilla (SFTP)

1. Протокол: **SFTP** (не FTP).
2. Хост: `207.154.238.178` (или `sftp://207.154.238.178`).
3. Порт: **22**.
4. Пользователь: **root**.
5. Вход: **файл ключа** → выбрать `id_rsa` (**без** `.pub`).  
   Если FileZilla просит `.ppk` — конвертировать через PuTTYgen: Load `id_rsa` → Save private key.
6. Пароль root **не обязателен**, если ключ принят сервером.

Типичная ошибка: вводят старый пароль при уже настроенном ключе — FileZilla «ругается», хотя правильный способ — **Key file**.

---

## 3) Если пароль «не тот» — восстановление через DigitalOcean

Делайте **только** для **своего** дроплета.

1. Войдите в [cloud.digitalocean.com](https://cloud.digitalocean.com).
2. **Droplets** → ваш сервер.
3. Вкладка **Access**:
   - **Launch Droplet Console** — веб-консоль (без SSH с ПК).
   - **Reset Root Password** — новый пароль придёт на email аккаунта DO.
4. В веб-консоли войдите как `root` с новым паролем из письма.
5. Добавьте **публичный** ключ с ПК в `authorized_keys` (текст ключа возьмите локально: `Get-Content $env:USERPROFILE\.ssh\id_rsa.pub`):

```bash
mkdir -p /root/.ssh
chmod 700 /root/.ssh
nano /root/.ssh/authorized_keys
# вставьте ОДНУ строку из id_rsa.pub, сохраните
chmod 600 /root/.ssh/authorized_keys
```

6. С ПК снова проверьте SSH (команда из §1) и FileZilla с ключом.

После успешного входа по ключу пароль из письма можно сменить (`passwd`), но **в чат и в git пароль не писать**.

---

## 4) Что лежит на сервере (напоминание)

| Инстанс | Папка | systemd |
|---------|--------|---------|
| Прод | `/root/PRD-BOT-ALL` | `trading_bot`, `telegram_signal_agent` |
| Песочница | `/root/AGENT-WORLD` | `trading_bot_agent_world`, `telegram_signal_agent_world` |

Секреты ботов — только в `.env` на сервере, не в `config.yaml` и не в GitHub.

---

## 5) Если снова потеряли доступ

1. Откройте этот файл в репозитории (`docs/server-access-ssh-filezilla.md`).
2. Сверьте IP в DigitalOcean.
3. Проверьте SSH с ключом; при отказе — Reset Root Password + `authorized_keys`.
4. FileZilla: SFTP + ключ `id_rsa`, порт 22.

Краткая хронология восстановления 30.07.2026 — в архиве чата (ссылка в начале файла).
