# Jab-rewards

Bot de Microsoft Rewards auto-arrancable, multi-usuario y con auto-reparación de selectores.

Repo: https://github.com/Jabry-Bot/ms-rewards-bot

## Instalación (cualquier usuario, Windows 10/11)

**Todo por ventanas, cero shell. Solo dos archivos:**

1. **`setup.exe`** — descárgalo **solo** desde [Releases](https://github.com/Jabry-Bot/ms-rewards-bot/releases) y ejecútalo. Es autocontenido (trae su propio Python) y se basta solo: instala Python, Git y Edge si faltan, **clona el repo** en una carpeta de instalación (así sigue recibiendo auto-updates por `git pull`), crea el `.venv`, instala dependencias, descarga el panel, **crea un acceso directo en el Escritorio**, configura la cuenta y registra la Scheduled Task. No necesitas nada preinstalado ni clonar a mano.
2. **`MsRewardsPanel.exe`** — el día a día (lo abres desde el acceso directo del Escritorio): ejecutar, estado en vivo, cambiar cuenta, login, registrar/quitar tarea, logs y **🧹 Desinstalar**. **Al abrirlo busca y aplica actualizaciones** automáticamente.

> Para dev / maintainer, o si el antivirus bloquea el `.exe`, puedes lanzar el instalador como Python puro: `python scripts/run_setup.py` (tras clonar el repo). No hay scripts de shell.

El instalador hace todo el trabajo:

- Instala Python 3.12 si falta (vía `winget` o instalador oficial silencioso).
- Crea `.venv` e instala dependencias (`patchright`, `httpx`, `pywin32`).
- **Sin consolas ni preguntas:** en la propia ventana eliges el **navegador** (`Chrome` o `Edge`; **Edge da más puntos**: bonus de búsquedas y racha de Edge) y pulsas **«Iniciar sesión y registrar tarea»**.
- Se abre el navegador del bot y **tú inicias sesión** con tu cuenta Microsoft (incluido el 2FA). El instalador lo detecta solo. **No se pide ni se guarda ninguna contraseña**: la sesión queda guardada en el perfil del bot durante semanas o meses, y si algún día caduca se renueva con **Login manual** en el panel.
- Crea los accesos directos **«Jab-rewards»** en el **Escritorio** y en el **menú Inicio**.
- Registra una Scheduled Task `MsRewardsBot` que arranca al iniciar sesión + diariamente a una hora aleatoria entre las 10:00 y las 14:00, y se reintenta cada 30 min durante unas horas.

A partir de ahí, el bot se ejecuta solo cada día. Si ya completó las búsquedas hoy, sale al instante sin abrir nada.

### Verificación antes de terminar

Que el bot "haga" una actividad no garantiza que Microsoft la acredite. Antes de cerrar, el bot comprueba en Rewards cada cosa que hace y repite lo que falte (todo excepto lo de móvil):

- **Búsquedas de PC:** lee el contador real (p. ej. `120/120`) y hace búsquedas nuevas, sin repetir consultas, hasta llegar al tope. El número de búsquedas también se calcula desde ese contador, no con una tabla por nivel.
- **Conjunto diario y "Ganar":** vuelve a consultar la API y repite las actividades que sigan sin acreditar.
- **Búsqueda por imagen y racha de Edge:** las lee de la sección *Rachas* de `/earn`; la búsqueda por imagen se repite si no consta.
- Deja un resumen `=== VERIFICACIÓN FINAL ===` en el log. Si falta algo importante (búsquedas, conjunto diario o búsqueda por imagen), **no da el día por terminado** y la tarea lo reintenta en 30 min (máx. 3 corridas al día). Las promociones con tareas (Spotify, Duolingo…) y alguna actividad de "Ganar" no se pueden acreditar desde el bot: se informan, pero no provocan reintentos.

### App de Bing: check-in y Leer para ganar (opcional)

Lo que solo da puntos desde la app de Bing del móvil (el **check-in** de la racha de la app y **Leer para ganar**, 3 puntos por artículo hasta 30 al día) lo puede hacer el bot desde el PC, hablando con la API de la propia app. Sin móvil ni emulador. Suma unos ~900 puntos al mes, más una racha extra que da sellos.

- **Activarlo:** en el panel, botón **📱 Vincular app de Bing** (una vez). Se abre una ventana normal del navegador y **tú** inicias sesión (Microsoft pide reautenticarse; el bot no mete tu contraseña). El permiso se guarda cifrado con DPAPI en `ms_rewards/state/app_token.bin` y se renueva solo. El panel muestra el estado de la app (sin vincular / hecho hoy / revincular). Desde consola: `run.py --link-app`.
- Desde entonces va en cada corrida diaria **en paralelo con las búsquedas** (solo habla con la API de la app, no usa el navegador), así que no la alarga aunque las lecturas vayan espaciadas como las de una persona (~1 por minuto). Aparece en el resumen `=== VERIFICACIÓN FINAL ===`. No provoca reintentos.
- `run.py --app-only` lo ejecuta suelto (no usa el navegador del bot). `MSR_APP_ACTIVITIES=0` lo apaga sin desvincular; para desvincular, borra `app_token.bin`.
- **Riesgo:** el bot se presenta como la app de Bing. Otros bots open source lo hacen desde hace tiempo, pero es una forma distinta de automatizar; por eso es opcional.

### No molesta mientras usas el PC

- **Sin ventanas de consola:** la tarea arranca `pythonw.exe`, que se relanza como `python.exe` con consola invisible (`ms_rewards/quiet.py`). Nada de lo que lanza después (git, el driver del navegador…) puede abrir una ventana ni robar el foco.
- **Pospone si estás jugando:** si hay un juego o una app a pantalla completa, la corrida automática se pospone y la tarea la reintenta en 30 min. A partir de las 21:00 ya no se pospone (para no perder el día; corre igualmente en segundo plano). Cámbialo con `MSR_GAME_DEADLINE_HOUR` (`24` = nunca mientras haya pantalla completa).
- **Navegador fuera de pantalla y sin sonido** (`--mute-audio`).
- Si la sesión cae de verdad, deja de reintentar tras 3 intentos ese día hasta que hagas **Login manual** en el panel.

## Modo maintainer

Solo en la máquina del maintainer (tú). Detecta cuando los selectores CSS del dashboard de Microsoft Rewards han cambiado y los repara automáticamente:

1. Requiere [Ollama](https://ollama.com/) corriendo en `localhost:11434` con un modelo cargado (default sugerido: `qwen2.5-coder:7b`).
2. Cuando el bot ve la lista de cards vacía pese a haber dashboard cargado, llama a Ollama con el HTML, recibe un selector candidato, lo valida offline contra el HTML, y si pasa lo persiste en `selectors.json`, bumpea `VERSION`, y hace `git commit && git push`.
3. Las demás máquinas reciben el fix en el siguiente arranque vía `git pull` automático.

Se activa **solo en local**, con un archivo `.env` en la raíz del repo (no se sube a git; plantilla en [`.env.example`](.env.example)). El instalador ya no lo pregunta:

```
MSR_MAINTAINER=1
MSR_OLLAMA_URL=http://localhost:11434
MSR_OLLAMA_MODEL=qwen2.5-coder:7b
```

## Panel de control (`MsRewardsPanel.exe`)

Interfaz gráfica moderna (customtkinter) que reemplaza por completo los `.bat` del día a día. Un solo panel con:

- **Ejecutar ahora** / **Solo daily** / **Solo búsquedas** (forzado).
- **Estado** en vivo: última corrida, puntos, nivel, búsquedas y estado de la Scheduled Task.
- **Cambiar cuenta** (borra la sesión y abre el navegador para que entres con la cuenta nueva).
- **Login manual** (abre el navegador para 2FA/captcha).
- **📱 Vincular app de Bing** (check-in + Leer para ganar; ver arriba) y su estado de hoy.
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
.venv\Scripts\python.exe run.py --link-app # vincula la app de Bing (inicias sesión tú)
.venv\Scripts\python.exe run.py --app-only # solo check-in + Leer para ganar
```

## Cambiar de cuenta

Botón **Cambiar cuenta** del panel: desconecta la cuenta actual (cierra el navegador del bot, borra la sesión, las credenciales antiguas y el permiso de la app de Bing) y abre el navegador para que **inicies sesión tú** con la cuenta nueva. Pide confirmación antes de borrar nada.

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
| `ms_rewards/setup_cli.py`        | Configura la cuenta sin consola (lo lanza `setup.exe`)          |
| `ms_rewards/winutil.py`          | Tarea programada, acceso directo, env y procesos (Python/pywin32)|

## Volatilidad de selectores

Investigado sobre bots open-source activos (TheNetsky/Microsoft-Rewards-Script, charlesbel/Microsoft-Rewards-Farmer):

- **~9 cambios críticos al año** en el dashboard / login.
- Frágiles: enlaces a cards del daily set, flow de login (URL cambia ocasionalmente), badge "completado".
- Estables (2+ años sin cambios): `#rqAnswerOption0/1` (this-or-that), `.rqOptionWrap` (quiz options).
- Pueden variar por región. La versión `es-ES` es la testeada en este repo.
