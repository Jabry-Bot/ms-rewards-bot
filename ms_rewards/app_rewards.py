"""
Actividades de la app de Bing (móvil) hechas desde el PC, por la API de la app:

  - Check-in diario: la racha "aplicación Bing" (type 103).
  - Leer para ganar: 3 puntos por artículo, hasta 30 al día (type 101).

Cómo funciona:
  La app de Bing habla con prod.rewardsplatform.microsoft.com usando un token
  OAuth de la cuenta Microsoft (cliente público de la propia app). Ese permiso
  se pide UNA vez con `run.py --link-app`: se abre una ventana normal del
  navegador (perfil temporal) y el usuario inicia sesión él mismo; la sesión
  del bot no basta, Microsoft pide reautenticarse. Se guarda el refresh token
  cifrado con DPAPI
  (state/app_token.bin, solo descifrable por este usuario de Windows en este
  PC) y en cada corrida se canjea por un access token sin abrir nada.

Es opcional: corre solo si la cuenta está vinculada (MSR_APP_ACTIVITIES=0 lo
apaga). Va al final de la corrida diaria; la verificación final lo resume.
Protocolo documentado a partir de bots open source que ya lo usan
(TheNetsky/Microsoft-Rewards-Script); el código es propio.
"""
from __future__ import annotations

import asyncio
import logging
import random
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

import config

log = logging.getLogger("app")
logging.getLogger("httpx").setLevel(logging.WARNING)   # no llenar el log con cada petición

try:
    import win32crypt  # type: ignore
except Exception:  # pragma: no cover - solo Windows
    win32crypt = None

# --- OAuth (cliente público de la app de Bing) ----------------------------
CLIENT_ID = "0000000040170455"
SCOPE = "service::prod.rewardsplatform.microsoft.com::MBI_SSL"
REDIRECT_URI = "https://login.live.com/oauth20_desktop.srf"
AUTHORIZE_URL = "https://login.live.com/oauth20_authorize.srf"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
TOKEN_PATH = config.APP_TOKEN_PATH

# --- API de la app -----------------------------------------------------------
PLATFORM = "https://prod.rewardsplatform.microsoft.com/dapi"
ANDROID_APP_VERSION = "34.5.450113001"
ANDROID_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko)"
              f" Chrome/142.0.0.0 Mobile Safari/537.36 BingWeb/{ANDROID_APP_VERSION}")
IOS_APP_VERSION = "33.4.440603001"
IOS_UA = ("Mozilla/5.0 (iPad; CPU iPad OS 26_5 like Mac OS X) AppleWebKit/605.1.15"
          " (KHTML, like Gecko) Version/26.5 Mobile/15E148 Safari/605.1.15"
          f" BingSapphire/{IOS_APP_VERSION}")

TYPE_READ = 101
TYPE_CHECKIN = 103

# Lecturas: pausa entre artículos (s) y tope de envíos por corrida.
READ_DELAY = (35.0, 75.0)
MAX_READS = 15
MAX_READ_MISSES = 3   # envíos seguidos sin que suba el progreso → se para


class AppAuthError(RuntimeError):
    pass


class AppApiError(RuntimeError):
    pass


# --- Token guardado (DPAPI) ------------------------------------------------
def is_linked() -> bool:
    return TOKEN_PATH.exists()


def unlink() -> None:
    TOKEN_PATH.unlink(missing_ok=True)


def _save_refresh(token: str) -> None:
    if win32crypt is None:
        raise AppAuthError("pywin32 no disponible: no se puede guardar el token cifrado")
    blob = win32crypt.CryptProtectData(token.encode("utf-8"), "ms_rewards app token",
                                       None, None, None, 0)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_bytes(blob)


def _load_refresh() -> str | None:
    if win32crypt is None or not TOKEN_PATH.exists():
        return None
    try:
        _, data = win32crypt.CryptUnprotectData(TOKEN_PATH.read_bytes(), None, None, None, 0)
        return data.decode("utf-8") or None
    except Exception as exc:
        log.warning("no se pudo descifrar el token de la app: %s", exc)
        return None


# --- OAuth -------------------------------------------------------------------
def authorize_url(state: str | None = None) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state or secrets.token_hex(16),
        "access_type": "offline_access",
    })


def code_from_url(url: str) -> str:
    """El `code` de la redirección final de OAuth ('' si no es esa página)."""
    try:
        u = urlparse(url)
    except ValueError:
        return ""
    if u.path.lower() != "/oauth20_desktop.srf":
        return ""
    return (parse_qs(u.query).get("code") or [""])[0]


async def _get_code(context, *, wait_s: float) -> str:
    """Abre la autorización en una pestaña; si hay sesión válida vuelve sola."""
    page = await context.new_page()
    try:
        await page.goto(authorize_url(), wait_until="domcontentloaded", timeout=30_000)
        code = code_from_url(page.url)
        if not code and wait_s > 0:
            try:
                await page.wait_for_url(lambda u: "/oauth20_desktop.srf" in u.lower(),
                                        timeout=wait_s * 1000)
            except Exception:
                pass
            code = code_from_url(page.url)
        return code
    except Exception as exc:
        log.debug("autorización de la app falló: %s", exc)
        return ""
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def _token_request(data: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.post(TOKEN_URL, data={"client_id": CLIENT_ID, "scope": SCOPE, **data})
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not body.get("access_token"):
        raise AppAuthError(f"{body.get('error', r.status_code)}: "
                           f"{str(body.get('error_description', ''))[:160]}")
    return body


def _keep(body: dict[str, Any]) -> str:
    """Guarda el refresh token (si viene, rota) y devuelve el access token."""
    if body.get("refresh_token"):
        _save_refresh(body["refresh_token"])
    return body["access_token"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _raise_window(pid: int) -> bool:
    """Pone delante la ventana del navegador recién abierto (sin robar el foco
    a la fuerza: topmost un instante y vuelta a normal)."""
    try:
        import win32con  # type: ignore  # noqa: PLC0415
        import win32gui  # type: ignore  # noqa: PLC0415
        import win32process  # type: ignore  # noqa: PLC0415
    except Exception:
        return False
    found: list[int] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            if win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                found.append(hwnd)
        return True

    try:
        win32gui.EnumWindows(_cb, None)
        flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
        for hwnd in found:
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
    except Exception:
        return False
    return bool(found)


async def _poll_code(port: int, proc: subprocess.Popen, wait_s: float) -> str:
    """Lee por DevTools las URLs de las pestañas hasta ver la redirección con code."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    raised = False
    async with httpx.AsyncClient(timeout=3) as http:
        while loop.time() < deadline:
            await asyncio.sleep(1.0)
            if not raised:
                raised = _raise_window(proc.pid)
            try:
                tabs = (await http.get(f"http://127.0.0.1:{port}/json/list")).json()
            except Exception:
                if proc.poll() is not None:     # el usuario cerró la ventana
                    return ""
                continue
            for tab in tabs if isinstance(tabs, list) else []:
                code = code_from_url(str(tab.get("url") or ""))
                if code:
                    return code
    return ""


def _close_browser(proc: subprocess.Popen, profile: Path) -> None:
    try:
        import winutil  # noqa: PLC0415
        winutil.kill_browser_processes(config.BROWSER_PROC, str(profile))
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    for _ in range(10):                 # el navegador tarda en soltar el perfil
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists():
            break
        time.sleep(0.5)


async def link_in_new_window(*, wait_s: float = 900) -> bool:
    """
    Vinculación interactiva. Abre una ventana NORMAL del navegador con un perfil
    temporal (no toca el del bot: funciona aunque haya una corrida en marcha).
    El usuario inicia sesión ahí; la URL final se lee por DevTools, se cierra la
    ventana y se borra el perfil temporal.
    """
    profile = Path(tempfile.mkdtemp(prefix="msr_link_"))
    port = _free_port()
    proc = subprocess.Popen(
        [config.CHROME_PATH, f"--user-data-dir={profile}", f"--remote-debugging-port={port}",
         "--no-first-run", "--no-default-browser-check", "--new-window",
         "--window-position=120,60", "--window-size=1000,820",
         "--disable-features=DefaultBrowserSettingEnforcement", authorize_url()],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        code = await _poll_code(port, proc, wait_s)
    finally:
        _close_browser(proc, profile)
    if not code:
        return False
    body = await _token_request({"grant_type": "authorization_code", "code": code,
                                 "redirect_uri": REDIRECT_URI})
    _keep(body)
    if not body.get("refresh_token"):
        log.warning("Microsoft no devolvió refresh token: el permiso caducará pronto")
    return True


async def access_token(context=None) -> str | None:
    """Access token para la API de la app, o None si la cuenta no está vinculada."""
    rt = _load_refresh()
    if rt:
        try:
            return _keep(await _token_request({"grant_type": "refresh_token",
                                               "refresh_token": rt}))
        except AppAuthError as exc:
            log.warning("no se pudo renovar el permiso de la app: %s", exc)
    if context is not None:   # sin interacción: solo funciona si la sesión basta
        code = await _get_code(context, wait_s=8)
        if code:
            try:
                return _keep(await _token_request({"grant_type": "authorization_code",
                                                   "code": code, "redirect_uri": REDIRECT_URI}))
            except AppAuthError as exc:
                log.warning("canje del permiso de la app falló: %s", exc)
    return None


# --- Cliente de la API de la app --------------------------------------------
def _locale() -> tuple[str, str]:
    lang, _, country = config.LOCALE.partition("-")
    return (country or "ES").upper(), (lang or "es").lower()


class AppClient:
    def __init__(self, http: httpx.AsyncClient, token: str):
        self.http = http
        self.token = token
        self.country, self.lang = _locale()

    def _headers(self, ios: bool) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.token}", "Accept": "*/*",
             "X-Rewards-Country": self.country, "X-Rewards-Language": self.lang,
             "X-Rewards-IsMobile": "true"}
        if ios:
            h.update({"User-Agent": IOS_UA, "X-Rewards-AppId": f"SAIOS/{IOS_APP_VERSION}",
                      "X-Rewards-PartnerId": "startapp", "X-Rewards-Flights": "rwgobig"})
        else:
            h.update({"User-Agent": ANDROID_UA,
                      "X-Rewards-AppId": f"SAAndroid/{ANDROID_APP_VERSION}",
                      "X-Rewards-PartnerId": "BingHomepage"})
        return h

    async def dashboard(self, *, ios: bool = False) -> dict[str, Any]:
        r = await self.http.get(f"{PLATFORM}/me",
                                params={"channel": "SAIOS" if ios else "SAAndroid",
                                        "options": "613"},
                                headers={**self._headers(ios), "Cache-Control": "no-cache"})
        body = _json(r)
        if body.get("code") != 0 or not isinstance(body.get("response"), dict):
            raise AppApiError(f"panel de la app: HTTP {r.status_code} code={body.get('code')}")
        return body["response"]

    async def submit(self, payload: dict[str, Any], *, ios: bool = False) -> dict[str, Any]:
        r = await self.http.post(f"{PLATFORM}/me/activities", json=payload,
                                 headers=self._headers(ios))
        body = _json(r)
        if r.status_code >= 400 or body.get("code") not in (0, None):
            raise AppApiError(f"actividad: HTTP {r.status_code} code={body.get('code')}")
        return body


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


# --- Promociones de la app ---------------------------------------------------
def offer_id(promo: dict) -> str:
    return str((promo.get("attributes") or {}).get("offerid") or promo.get("name") or "").strip()


def promo_kind(promo: dict) -> str:
    attrs = promo.get("attributes") or {}
    kind = str(attrs.get("type") or "").strip().lower()
    oid = offer_id(promo).lower()
    if kind == "checkin" or oid == "gamification_sapphire_dailycheckin":
        return "checkin"
    if kind == "msnreadearn" or "readarticle" in oid:
        return "read"
    return kind


def find_promo(dashboard: dict | None, kind: str) -> dict | None:
    for promo in (dashboard or {}).get("promotions") or []:
        if isinstance(promo, dict) and promo_kind(promo) == kind:
            return promo
    return None


def _num(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def read_progress(promo: dict | None) -> tuple[int, int] | None:
    attrs = (promo or {}).get("attributes") or {}
    prog, mx = _num(attrs.get("pointprogress")), _num(attrs.get("pointmax"))
    if prog is None or mx is None:
        return None
    return prog, mx


def is_complete(promo: dict | None) -> bool:
    attrs = (promo or {}).get("attributes") or {}
    if str(attrs.get("complete") or "").strip().lower() == "true":
        return True
    pr = read_progress(promo)
    return pr is not None and pr[1] > 0 and pr[0] >= pr[1]


def mobile_streak_day(dashboard: dict | None) -> int | None:
    """Día de la racha "aplicación Bing" (level_info.hva_dailystreaks_mobile_days).
    Es lo que mueve el check-in hoy; la promo Gamification_Sapphire_DailyCheckIn
    es del sistema antiguo y ya no se actualiza (última fecha: 2024)."""
    for promo in (dashboard or {}).get("promotions") or []:
        if isinstance(promo, dict) and promo.get("name") == "level_info":
            return _num((promo.get("attributes") or {}).get("hva_dailystreaks_mobile_days"))
    return None


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(text.split(".")[0], "%m/%d/%Y %I:%M:%S %p")
        except ValueError:
            return None
    return dt


def checked_in_today(promo: dict | None, today: datetime | None = None) -> bool:
    """last_updated del check-in es de hoy (hora local)."""
    dt = _parse_dt(((promo or {}).get("attributes") or {}).get("last_updated"))
    if dt is None:
        return False
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.date() == (today or datetime.now()).date()


# --- Actividades ---------------------------------------------------------------
async def daily_checkin(client: AppClient, *, done_today: bool = False) -> dict[str, Any]:
    """Check-in diario. `done_today`: la racha de /earn ya marca "Check-in 1/1"
    (el panel de la app no dice si es de hoy, así que eso lo decide la web)."""
    if done_today:
        log.info("check-in de la app: ya hecho hoy (racha de /earn) ✓")
        return {"status": "done", "points": 0}
    try:
        before = await client.dashboard(ios=True)
    except Exception as exc:
        log.warning("check-in: no se pudo leer el panel de la app: %s", exc)
        before = None
    promo = find_promo(before, "checkin")
    if promo is not None and (is_complete(promo) or checked_in_today(promo)):
        log.info("check-in de la app: ya hecho hoy ✓")
        return {"status": "done", "points": 0}
    balance0 = (before or {}).get("balance")

    payload = {"risk_context": {}, "type": TYPE_CHECKIN, "channel": "SAIOS", "attributes": {},
               "id": str(uuid.uuid4()), "amount": 1, "country": client.country}
    try:
        await client.submit(payload, ios=True)
    except Exception as exc:
        log.warning("check-in: el envío dio error (%s); compruebo si contó igualmente", exc)

    progress0 = _num(((promo or {}).get("attributes") or {}).get("progress"))
    day0 = mobile_streak_day(before)
    for _ in range(5):
        await asyncio.sleep(random.uniform(2.0, 3.5))
        try:
            after = await client.dashboard(ios=True)
        except Exception:
            continue
        gained = (after.get("balance") - balance0
                  if isinstance(after.get("balance"), int) and isinstance(balance0, int) else None)
        p2 = find_promo(after, "checkin")
        progress1 = _num(((p2 or {}).get("attributes") or {}).get("progress"))
        advanced = progress0 is not None and progress1 is not None and progress1 > progress0
        day1 = mobile_streak_day(after)
        streak_moved = day0 is not None and day1 is not None and day1 != day0   # 7→1 al reiniciar
        # El saldo solo cuenta como prueba si no hay otra señal legible: con
        # otra actividad en marcha (búsquedas) sube por su cuenta.
        weak = p2 is None and day1 is None and (gained or 0) >= 5
        if streak_moved or checked_in_today(p2) or is_complete(p2) or advanced or weak:
            log.info("check-in de la app: hecho ✓ (racha día %s→%s)", day0, day1)
            return {"status": "ok", "points": gained, "streak_day": day1}
    log.warning("check-in de la app: enviado pero SIN confirmar (racha día %s)", day0)
    return {"status": "unconfirmed", "points": None, "streak_day": day0}


async def read_to_earn(client: AppClient, *, delay: tuple[float, float] = READ_DELAY,
                       max_reads: int = MAX_READS) -> dict[str, Any]:
    try:
        dash = await client.dashboard()
    except Exception as exc:
        log.warning("Leer para ganar: no se pudo leer el panel de la app: %s", exc)
        return {"status": "error", "progress": None}
    promo = find_promo(dash, "read")
    if promo is None:
        log.info("Leer para ganar: no disponible en esta cuenta")
        return {"status": "unavailable", "progress": None}
    prog = read_progress(promo)
    if is_complete(promo):
        log.info("Leer para ganar: ya completo (%s) ✓", _fmt(prog))
        return {"status": "done", "progress": list(prog) if prog else None, "points": 0}

    oid = offer_id(promo)
    start = prog[0] if prog else None
    log.info("Leer para ganar: %s — empiezo (offer %s)", _fmt(prog), oid)
    misses = 0
    for n in range(1, max_reads + 1):
        payload = {"id": secrets.token_hex(64), "amount": 1, "type": TYPE_READ,
                   "attributes": {"offerid": oid}, "country": client.country}
        try:
            await client.submit(payload)
        except Exception as exc:
            log.info("lectura %d: el envío dio error (%s)", n, exc)
        await asyncio.sleep(random.uniform(1.5, 3.0))
        try:
            promo2 = find_promo(await client.dashboard(), "read")
        except Exception:
            promo2 = None
        p2 = read_progress(promo2)
        advanced = bool(p2 and prog and p2[0] > prog[0])
        log.info("lectura %d: %s%s", n, _fmt(p2), "" if advanced else " (sin subir)")
        if p2:
            prog = p2
        if is_complete(promo2):
            break
        misses = 0 if advanced else misses + 1
        if misses >= MAX_READ_MISSES:
            log.warning("Leer para ganar: %d lecturas seguidas sin sumar — paro", misses)
            break
        await asyncio.sleep(random.uniform(*delay))

    done = prog is not None and prog[1] > 0 and prog[0] >= prog[1]
    points = prog[0] - start if prog and start is not None else None
    log.info("Leer para ganar: %s %s (+%s)", _fmt(prog), "✓" if done else "✗",
             points if points is not None else "?")
    return {"status": "ok" if done else "incomplete",
            "progress": list(prog) if prog else None, "points": points}


def _fmt(pair: tuple[int, int] | None) -> str:
    return f"{pair[0]}/{pair[1]}" if pair else "?"


async def checkin_done_on_web(context) -> bool:
    """La racha "aplicación Bing" de /earn ya marca el check-in de hoy (1/1).
    El panel de la app no dice si el check-in es de hoy; la web sí."""
    try:
        import verify  # noqa: PLC0415
        streak = (await verify.read_streaks(context)).get("app")
    except Exception as exc:
        log.debug("no se pudo leer la racha de la app en /earn: %s", exc)
        return False
    return bool(streak and streak[1] > 0 and streak[0] >= streak[1])


async def run_app_activities(context=None, *, done_today: bool | None = None) -> dict[str, Any]:
    """
    Check-in + Leer para ganar. Devuelve un dict serializable para last_run.json.

    Con context=None NO toca el navegador del bot (ni para leer /earn ni para
    revincular): así puede ir en paralelo con las búsquedas sin quitarles la
    pestaña. `done_today` viene entonces de checkin_done_on_web() hecho antes.
    """
    log.info("=== APP DE BING (check-in + Leer para ganar) ===")
    token = await access_token(context)
    if not token:
        log.warning("app de Bing: sin permiso válido — vincula la cuenta con "
                    "'run.py --link-app' (una vez)")
        return {"linked": False}
    if done_today is None:
        done_today = await checkin_done_on_web(context) if context is not None else False
    async with httpx.AsyncClient(timeout=30) as http:
        client = AppClient(http, token)
        result: dict[str, Any] = {"linked": True}
        result["checkin"] = await daily_checkin(client, done_today=done_today)
        await asyncio.sleep(random.uniform(3.0, 8.0))
        result["read"] = await read_to_earn(client)
    return result


def summarize(result: dict[str, Any] | None, today: str | None = None) -> dict[str, Any] | None:
    """
    Resumen compacto de run_app_activities() para last_run.json (lo lee el panel).

    {"date", "linked", "checkin": ok|done|unconfirmed|error, "streak_day",
     "read": [hecho, máx] | None, "read_status": ok|done|incomplete|unavailable|error}
    """
    if not isinstance(result, dict):
        return None
    out: dict[str, Any] = {"date": today or datetime.now().date().isoformat(),
                           "linked": bool(result.get("linked"))}
    if not out["linked"]:
        return out
    checkin = result.get("checkin") or {}
    read = result.get("read") or {}
    out["checkin"] = checkin.get("status") or "error"
    if checkin.get("streak_day") is not None:
        out["streak_day"] = checkin["streak_day"]
    out["read"] = read.get("progress")
    out["read_status"] = read.get("status") or "error"
    return out
