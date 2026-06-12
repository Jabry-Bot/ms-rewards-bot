# ms_rewards

Bot de Microsoft Rewards auto-arrancable, multi-usuario y con auto-reparación de selectores.

Repo: https://github.com/Jabry-Bot/ms-rewards-bot

## Instalación (cualquier usuario, Windows 10/11)

**Todo por ventanas, cero shell. Solo dos archivos:**

1. **`setup.exe`** — descárgalo **solo** desde [Releases](https://github.com/Jabry-Bot/ms-rewards-bot/releases) y ejecútalo. Es autocontenido (trae su propio Python) y se basta solo: instala Python, Git y Edge si faltan, **clona el repo** en una carpeta de instalación (así sigue recibiendo auto-updates por `git pull`), crea el `.venv`, instala dependencias, descarga el panel, **crea un acceso directo en el Escritorio**, configura la cuenta y registra la Scheduled Task. No necesitas nada preinstalado ni clonar a mano.
2. **`MsRewardsPanel.exe`** — el día a día (lo abres desde el acceso directo del Escritorio): ejecutar, estado en vivo, cambiar cuenta, login, registrar/quitar tarea, logs y **🧹 Desinstalar**. **Al abrirlo busca y aplica actualizaciones** automáticamente.

> Para usuarios avanzados o si el antivirus bloquea el `.exe`, hay un instalador por consola en [`scripts/setup.bat`](scripts/setup.bat) (requiere clonar el repo con git primero).

El instalador hace todo el trabajo:

- Instala Python 3.12 si falta (vía `winget` o instalador oficial silencioso).
- Crea `.venv` e instala dependencias (`patchright`, `httpx`, `pywin32`).
- Te pide:
  - **USER_ID** identificador para esta máquina (default = hostname).
  - **Navegador** a usar: `chrome` o `edge` (default `chrome`). Ambos funcionan igual; **Edge da el bonus de búsquedas de Microsoft Rewards**, así que rinde más puntos. Requiere tener ese navegador instalado.
  - **Email y contraseña** de tu cuenta Microsoft. Se guardan cifrados localmente con DPAPI; nunca salen de tu PC.
  - Si esta máquina es del **maintainer** (auto-fix con Ollama). Si no sabes lo que es, di que no.
- Abre Chrome y hace login automáticamente.
  - **La primera vez Microsoft puede pedir 2FA / captcha**. Completa la verificación en la ventana de Chrome y pulsa Enter en la consola. Después, la sesión queda guardada en el perfil durante semanas o meses.
- Registra una Scheduled Task `MsRewardsBot` que arranca al iniciar sesión + diariamente a una hora aleatoria entre las 10:00 y las 14:00.

A partir de ahí, el bot se ejecuta solo cada día. Si ya completó las búsquedas hoy, no hace nada.

## Modo maintainer

Solo en la máquina del maintainer (tú). Detecta cuando los selectores CSS del dashboard de Microsoft Rewards han cambiado y los repara automáticamente:

1. Requiere [Ollama](https://ollama.com/) corriendo en `localhost:11434` con un modelo cargado (default sugerido: `qwen2.5-coder:7b`).
2. Cuando el bot ve la lista de cards vacía pese a haber dashboard cargado, llama a Ollama con el HTML, recibe un selector candidato, lo valida offline contra el HTML, y si pasa lo persiste en `selectors.json`, bumpea `VERSION`, y hace `git commit && git push`.
3. Las demás máquinas reciben el fix en el siguiente arranque vía `git pull` automático.

Variables de entorno relevantes:

- `MSR_MAINTAINER=1`
- `MSR_OLLAMA_URL=http://localhost:11434`
- `MSR_OLLAMA_MODEL=qwen2.5-coder:7b`

## Panel de control (`MsRewardsPanel.exe`)

Interfaz gráfica moderna (customtkinter) que reemplaza por completo los `.bat` del día a día. Un solo panel con:

- **Ejecutar ahora** / **Solo daily** / **Solo búsquedas** (forzado).
- **Estado** en vivo: última corrida, puntos, nivel, búsquedas y estado de la Scheduled Task.
- **Cambiar cuenta** (pide email/contraseña en un diálogo y hace el reset + login).
- **Login manual** (abre el navegador para 2FA/captcha).
- **Registrar / Quitar** la tarea programada.
- **🧹 Desinstalar** (quita tarea + borra estado/credenciales/perfil/variables, con confirmación).
- **Log en vivo** del proceso en marcha, con botón de cancelar.
- **Auto-update al abrir:** comprueba y aplica `git pull` automáticamente cada vez que se inicia.

El `.exe` es solo el front-end: invoca `ms_rewards\.venv\Scripts\python.exe run.py` por debajo. Necesita `setup.exe` ejecutado antes (crea el `.venv`).

> **Dev / maintainer:** para lanzar las GUIs sin empaquetar, `python scripts/run_panel.py`. Para reconstruir los `.exe`, [`scripts/build_panel_exe.bat`](scripts/build_panel_exe.bat) y [`scripts/build_setup_exe.bat`](scripts/build_setup_exe.bat). Ver [`scripts/README.md`](scripts/README.md).

## Uso manual

Tras el setup, puedes invocar el bot a mano:

```
cd ms_rewards
.venv\Scripts\python.exe run.py            # daily + searches
.venv\Scripts\python.exe run.py --daily    # solo daily
.venv\Scripts\python.exe run.py --searches # solo searches
.venv\Scripts\python.exe run.py --force    # ignora el "ya completado hoy"
.venv\Scripts\python.exe run.py --no-update # salta git pull
.venv\Scripts\python.exe run.py --setup    # abre Chrome solo para login manual
.venv\Scripts\python.exe run.py --kill     # mata chrome.exe del bot
```

## Cambiar de cuenta

Botón **Cambiar cuenta** del panel: desconecta la cuenta actual (cierra el navegador del bot, borra la sesión y las credenciales cifradas) y lanza el login para una cuenta nueva. Pide confirmación antes de borrar nada.

## Desinstalar

Botón **🧹 Desinstalar** del panel: elige qué quitar (tarea programada, estado, credenciales, perfil del navegador, variables `MSR_*`) con confirmación. Para borrar TODO, elimina además la carpeta del bot a mano. (Fallback por consola: [`scripts/uninstall.bat`](scripts/uninstall.bat).)

## Estructura

| Archivo                          | Función                                                         |
|----------------------------------|-----------------------------------------------------------------|
| `setup.exe`                      | Instalador de un clic (Python/Git/Edge + venv + clone + cuenta) |
| `MsRewardsPanel.exe`             | Panel de control del día a día (GUI)                            |
| `installer/`                     | Código del instalador (`bootstrap.py` lógica + `app.py` GUI)    |
| `panel/`                         | Código del panel (`core.py` lógica + `app.py` GUI)             |
| `scripts/`                       | Build de los `.exe` + fallbacks de consola (dev/maintainer)     |
| `tests/`                         | Tests (pytest) de la lógica del panel y del instalador          |
| `ms_rewards/run.py`              | Orquestador (idempotencia + update + daily + searches + heal)   |
| `ms_rewards/uninstall.py`        | Desinstalador no interactivo (invocado desde el panel)          |
| `ms_rewards/launcher.py`         | Lanza Chrome real vía patchright                                |
| `ms_rewards/daily.py`            | Resuelve daily set / more activities / punch cards              |
| `ms_rewards/searches.py`         | 30+ búsquedas humanizadas en Bing                               |
| `ms_rewards/login.py`            | Autologin Microsoft (con fallback manual a 2FA)                 |
| `ms_rewards/selectors.json`      | Única fuente de verdad de selectores CSS volátiles              |
| `ms_rewards/selectors.py`        | API para leer/actualizar `selectors.json`                       |
| `ms_rewards/healer.py`           | Auto-fix con Ollama (solo maintainer)                           |
| `ms_rewards/updater.py`          | git pull condicional por `VERSION`                              |
| `ms_rewards/runstate.py`         | Estado diario (idempotencia)                                    |
| `ms_rewards/credentials.py`      | Cifrado DPAPI de email/pass                                     |
| `ms_rewards/setup_cli.py`        | CLI interactivo de cuenta (invocado por `setup.exe`)            |
| `ms_rewards/scheduler/`          | Scripts PowerShell para gestionar la tarea programada           |

## Volatilidad de selectores

Investigado sobre bots open-source activos (TheNetsky/Microsoft-Rewards-Script, charlesbel/Microsoft-Rewards-Farmer):

- **~9 cambios críticos al año** en el dashboard / login.
- Frágiles: enlaces a cards del daily set, flow de login (URL cambia ocasionalmente), badge "completado".
- Estables (2+ años sin cambios): `#rqAnswerOption0/1` (this-or-that), `.rqOptionWrap` (quiz options).
- Pueden variar por región. La versión `es-ES` es la testeada en este repo.
