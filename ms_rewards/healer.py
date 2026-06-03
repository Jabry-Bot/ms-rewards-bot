"""
Auto-reparación de selectores con LLM local (Ollama).

Se activa SOLO en la máquina del maintainer (MSR_MAINTAINER=1). Cuando el
bot detecta selectores rotos durante la ejecución (lista de cards vacía en
un dashboard que sí cargó, quiz sin opciones reconocibles, etc.), llama a
heal() pasándole la clave del selector que falló y el HTML de la página
donde falló.

heal() pide a Ollama un selector CSS candidato, lo valida contra el HTML
ofreciéndole una prueba offline (Playwright cargado con set_content),
y si pasa la validación lo persiste en selectors.json, bumpea VERSION, y
hace commit + push para que los demás usuarios reciban el fix.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
from patchright.async_api import async_playwright

import config
import selectors

log = logging.getLogger("healer")

_REPO_ROOT = Path(__file__).parent
_VERSION_FILE = _REPO_ROOT / "VERSION"
_SELECTORS_FILE = _REPO_ROOT / "selectors.json"

# Pista mínima por clave: qué buscamos exactamente. El healer la inyecta
# en el prompt para que el LLM sepa qué descripciones de elementos son
# matches válidos.
_HINTS: dict[str, str] = {
    "dashboard.card_container": (
        "Cada elemento contenedor de una 'card' del dashboard de Microsoft "
        "Rewards. Suele haber daily set, more activities y punch cards. "
        "Cada card contiene un <a> que abre la actividad."
    ),
    "dashboard.card_link": (
        "El <a> dentro de cada card del dashboard que abre la actividad "
        "(daily set, more activities, punch card)."
    ),
    "dashboard.complete_badge": (
        "El icono que indica que una card ya está completada (típicamente "
        "un check verde / circulo con tick)."
    ),
    "quiz.options": (
        "Los botones/elementos de opción de respuesta en un quiz multi-"
        "respuesta de Microsoft Rewards (no this-or-that ni poll)."
    ),
    "quiz.this_or_that": (
        "Las dos opciones de un mini-juego 'This or That' en Microsoft "
        "Rewards (dos imágenes/cards lado a lado para elegir una)."
    ),
    "quiz.poll": "Las opciones de un poll/encuesta en Microsoft Rewards.",
    "punch_card.piece": (
        "Cada pieza/link clickable dentro del hub de una punch card semanal "
        "que abre una sub-actividad. Las completadas suelen tener clase "
        "que incluye 'complete'."
    ),
}


def _trim_html(html: str, max_bytes: int = 60_000) -> str:
    """Recorta scripts, styles y comments, y limita el tamaño total."""
    h = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    h = re.sub(r"<style[\s\S]*?</style>", "", h, flags=re.IGNORECASE)
    h = re.sub(r"<!--[\s\S]*?-->", "", h)
    # Quedarnos con la parte de body si la hay
    m = re.search(r"<body[\s\S]*</body>", h, flags=re.IGNORECASE)
    if m:
        h = m.group(0)
    if len(h) > max_bytes:
        h = h[:max_bytes] + "\n<!-- ...truncado... -->"
    return h


def _build_prompt(failed_key: str, current_selector: str, html: str) -> str:
    hint = _HINTS.get(failed_key, "(sin pista específica para esta clave)")
    return f"""Eres un experto en selectores CSS. Te paso un fragmento de HTML del
dashboard de Microsoft Rewards. El selector CSS actual ha dejado de funcionar
y necesito que propongas uno nuevo.

CLAVE QUE FALLA: {failed_key}
QUÉ BUSCAMOS: {hint}
SELECTOR ACTUAL (que ya no funciona): {current_selector}

REGLAS:
1) Devuelve EXCLUSIVAMENTE un objeto JSON válido con esta forma exacta:
   {{"selector": "<css selector>", "reasoning": "<por qué este selector>"}}
2) El selector debe ser CSS estándar (no XPath).
3) Puedes usar múltiples selectores separados por coma como fallback.
4) NO incluyas texto fuera del JSON. NO incluyas markdown, ni ```.

HTML:
{html}
"""


def _call_ollama(prompt: str) -> dict[str, Any] | None:
    """Llama a /api/generate de Ollama. Devuelve el JSON parseado o None."""
    url = f"{config.OLLAMA_URL.rstrip('/')}/api/generate"
    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 400},
    }
    try:
        with httpx.Client(timeout=180.0) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.exception("llamada a Ollama falló: %s", exc)
        return None
    raw = (data.get("response") or "").strip()
    # Extraer el primer bloque JSON del output
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        log.warning("respuesta de Ollama sin JSON: %r", raw[:200])
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        # Intento de limpieza: quitar trailing commas
        cleaned = re.sub(r",\s*([}\]])", r"\1", m.group(0))
        try:
            obj = json.loads(cleaned)
        except Exception:
            log.warning("JSON no parseable: %r", m.group(0)[:200])
            return None
    if not isinstance(obj, dict) or "selector" not in obj:
        log.warning("JSON sin 'selector': %r", obj)
        return None
    return obj


async def _validate_selector(html: str, selector: str) -> int:
    """Carga el HTML offline en Chromium y cuenta matches del selector."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.set_content(html, wait_until="domcontentloaded", timeout=10_000)
            count = await page.evaluate(
                "(sel) => document.querySelectorAll(sel).length", selector
            )
            return int(count or 0)
        except Exception as exc:
            log.warning("validación de selector falló: %s", exc)
            return 0
        finally:
            await browser.close()


def _git(*args: str, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except Exception as exc:
        return -1, str(exc)


def _persist_and_publish(failed_key: str, new_selector: str, old_selector: str) -> str | None:
    """Actualiza selectors.json, sincroniza VERSION, commit + push. Devuelve la nueva version o None."""
    new_v = selectors.update_and_persist(failed_key, new_selector, bump="patch")
    try:
        _VERSION_FILE.write_text(new_v + "\n", encoding="utf-8")
    except Exception as exc:
        log.warning("no se pudo escribir VERSION: %s", exc)
        return None

    # git add + commit + push
    code, out = _git("add", str(_SELECTORS_FILE.name), str(_VERSION_FILE.name))
    if code != 0:
        log.warning("git add falló: %s", out)
        return None
    code, out = _git("commit", "-m", f"heal: {failed_key} -> {new_selector[:60]}")
    if code != 0:
        log.warning("git commit falló (puede que no haya cambios): %s", out)
        return None
    code, out = _git("push", timeout=60)
    if code != 0:
        log.warning("git push falló: %s (cambio sigue commiteado local)", out)
    # log dedicado
    try:
        log_path = config.LOG_DIR / "heal.log"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "key": failed_key,
                        "old": old_selector,
                        "new": new_selector,
                        "version": new_v,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    return new_v


async def heal(failed_key: str, page_html: str, page_url: str) -> dict | None:
    """
    Intenta reparar el selector `failed_key` usando Ollama. Devuelve un dict
    con los detalles del fix si tuvo éxito, None si no.
    """
    if not config.MAINTAINER:
        log.info("modo no-maintainer: no se ejecuta heal")
        return None

    old_selector = ""
    try:
        old_selector = selectors.get(failed_key)
    except KeyError:
        log.warning("clave desconocida en selectors.json: %s", failed_key)
        return None

    trimmed = _trim_html(page_html)
    prompt = _build_prompt(failed_key, old_selector, trimmed)
    log.info("invocando Ollama (%s) para %s", config.OLLAMA_MODEL, failed_key)
    proposal = _call_ollama(prompt)
    if not proposal:
        return None
    new_selector = proposal["selector"].strip()
    if not new_selector or new_selector == old_selector:
        log.info("propuesta vacía o idéntica a la actual — no se aplica")
        return None

    matches = await _validate_selector(page_html, new_selector)
    log.info("selector candidato %r -> %d matches", new_selector, matches)
    if matches < 1:
        log.info("validación falló (0 matches), descartando")
        return None

    new_v = _persist_and_publish(failed_key, new_selector, old_selector)
    if not new_v:
        return None
    log.info("heal OK: %s -> %s (v%s)", failed_key, new_selector, new_v)
    return {
        "key": failed_key,
        "old": old_selector,
        "new": new_selector,
        "version": new_v,
        "matches": matches,
    }
