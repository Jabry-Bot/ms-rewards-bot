# ms_rewards

Bot de Microsoft Rewards auto-arrancable, multi-usuario y con auto-reparación de selectores.

Repo: https://github.com/Jabry-Bot/ms-rewards-bot

## Instalación (cualquier usuario, Windows 10/11)

```cmd
git clone https://github.com/Jabry-Bot/ms-rewards-bot.git
cd ms-rewards-bot
setup.bat
```

O descarga el ZIP desde GitHub y ejecuta `setup.bat`. Es **importante clonar con git** si quieres recibir actualizaciones automáticas — el bot hace `git pull` antes de cada ejecución cuando hay una versión nueva.

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

```cmd
switch_account.bat
```

Desconecta la cuenta actual (cierra el Chrome del bot, borra la sesión guardada y las credenciales cifradas) y lanza el login para una cuenta nueva. Pide confirmación antes de borrar nada.

`setup.bat` hace ahora exactamente el mismo reset robusto antes del login (cierra el Chrome del bot, espera, borra el perfil viejo + credenciales + estado, y **fuerza** el login con la cuenta que indiques), así que re-ejecutarlo **sí** sirve para cambiar de cuenta. La diferencia es que `setup.bat` además instala dependencias y registra la Scheduled Task; `switch_account.bat` es el atajo cuando solo quieres cambiar de cuenta.

## Desinstalar

```
powershell -ExecutionPolicy Bypass -File ms_rewards\scheduler\uninstall_task.ps1
```

Y borra el directorio. Las credenciales viven en `ms_rewards\state\credentials.bin`.

## Estructura

| Archivo                          | Función                                                         |
|----------------------------------|-----------------------------------------------------------------|
| `setup.bat`                      | Instalador end-to-end                                           |
| `ms_rewards/run.py`              | Orquestador (idempotencia + update + daily + searches + heal)   |
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
| `ms_rewards/setup_cli.py`        | CLI interactivo invocado desde `setup.bat`                      |
| `ms_rewards/scheduler/`          | Scripts PowerShell para gestionar la tarea programada           |

## Volatilidad de selectores

Investigado sobre bots open-source activos (TheNetsky/Microsoft-Rewards-Script, charlesbel/Microsoft-Rewards-Farmer):

- **~9 cambios críticos al año** en el dashboard / login.
- Frágiles: enlaces a cards del daily set, flow de login (URL cambia ocasionalmente), badge "completado".
- Estables (2+ años sin cambios): `#rqAnswerOption0/1` (this-or-that), `.rqOptionWrap` (quiz options).
- Pueden variar por región. La versión `es-ES` es la testeada en este repo.
