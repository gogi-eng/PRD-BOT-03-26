---
name: notes-before-razrabotal
description: >-
  DocAgent: абзац «Примечание…» в конец перед «Разработал:»; всегда добавлять
  строку «Разработал:» перед подписантом. Use when fixing RI/DI tail.
---

# Примечания и «Разработал:»

1. Весь абзац, начинающийся с «Примечание» / «Примечания» / «Прим.» →  
   конец документа, сразу перед `Разработал:`.
2. Строка `Разработал:` **обязательна**. Если отсутствует — вставить  
   (`ensure_razrabotal_heading`).

## Код

`move_notes_before_razrabotal`, `ensure_razrabotal_heading`,  
`finalize_notes_and_signatories`
