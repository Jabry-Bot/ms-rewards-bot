"""
Búsqueda diaria por imagen (Bing Visual Search) para mantener la racha nueva
de Microsoft Rewards.

Microsoft añadió una racha diaria por hacer UNA búsqueda visual (buscar con una
imagen). El flujo de usuario es: bing.com/images → icono cámara del cuadro de
búsqueda (#sb_sbi, "Buscar con una imagen") → subir una imagen local a un
input[type=file] (#sb_fileinput) → Bing navega a la página de resultados de
búsqueda visual (/images/search?...&view=detailV2...). Esa navegación es la que
registra la actividad/racha.

Diseño (el más fiable con patchright):
  - Escribimos un PNG mínimo válido (embebido aquí en base64, sin binario que
    commitear ni dependencia de rutas de assets del .exe) a un fichero temporal
    y lo subimos con set_input_files sobre el input[type=file] que bing.com/images
    monta para la búsqueda por imagen. patchright rellena el input aunque esté
    oculto tras el icono cámara, evitando el diálogo nativo del SO.
  - Reusa la sesión del perfil persistente y mantiene la pestaña en primer plano:
    como el daily set, el crédito exige sesión + página visible.
  - Humanizado con los mismos helpers que searches.py.

Devuelve True si la búsqueda visual se disparó (navegación a la página de
resultados), False si no. NO garantiza el crédito de la racha (eso se confirma
en el navegador real); True significa "la búsqueda visual se ejecutó".
"""
from __future__ import annotations

import base64
import logging
import os
import random
import tempfile
from pathlib import Path

from patchright.async_api import BrowserContext, Page

import selectors
from humanize import human_read, human_scroll, random_mouse_drift, safe_goto, sleep_jitter

log = logging.getLogger("visual_search")

# Entrada canónica de la búsqueda visual de Bing.
_IMAGES_URL = "https://www.bing.com/images"

# Imagen a subir. Preferimos un asset REAL del repo (assets/logo.png): Bing
# necesita una imagen con contenido reconocible para producir una SERP de
# búsqueda visual (un PNG 1x1 no genera resultados). El bot corre desde el repo
# clonado, así que assets/ está disponible. Fallback: PNG mínimo embebido.
_ASSET_IMG = Path(__file__).resolve().parent.parent / "assets" / "logo.png"

# PNG 1x1 gris válido (embebido) como último recurso si falta el asset.
_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _write_temp_image() -> str:
    """Escribe el PNG embebido a un fichero temporal y devuelve su ruta."""
    data = base64.b64decode(_PNG_1X1_B64)
    fd, path = tempfile.mkstemp(prefix="msr_visual_", suffix=".png")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise
    return path


def _image_path() -> tuple[str, bool]:
    """Devuelve (ruta_imagen, es_temporal). Usa el asset real si existe."""
    if _ASSET_IMG.exists():
        return str(_ASSET_IMG), False
    return _write_temp_image(), True


async def _find_file_input(page: Page):
    """
    Localiza el input[type=file] de la búsqueda por imagen. Bing lo mantiene en
    el DOM (a veces oculto tras el icono cámara). Si no está de entrada, intenta
    revelarlo clicando el icono cámara (#sb_sbi / .sbi_camera / aria).
    """
    sel = selectors.get("bing.visual_file_input", "input[type='file']")
    el = await page.query_selector(sel)
    if el:
        return el
    cam_sel = selectors.get(
        "bing.visual_camera",
        "#sb_sbi, .sbi_camera, [aria-label*='visual' i], [aria-label*='imagen' i], [aria-label*='image' i]",
    )
    try:
        cam = await page.query_selector(cam_sel)
        if cam:
            await cam.click(timeout=4000)
            await sleep_jitter(1.0, 2.2)
    except Exception as exc:
        log.debug("click icono cámara falló: %s", exc)
    return await page.query_selector(sel)


async def _confirm_visual_results(page: Page) -> bool:
    """
    Confirma que la búsqueda visual se realizó. Tras subir la imagen, Bing sube
    el blob (POST /images/kblob?iss=sbiupload) y navega a la SERP de la búsqueda
    visual, típicamente /search?q=...&FORM=SBIIRP&bcid=... (o /images/search con
    sbisrc). Detectamos esos marcadores en la URL.
    """
    markers = ("form=sbiirp", "sbisrc", "/images/search", "bcid=")
    try:
        await page.wait_for_url(
            lambda u: any(m in (u or "").lower() for m in markers), timeout=15_000)
        return True
    except Exception:
        url = (page.url or "").lower()
        return any(m in url for m in markers)


async def run_visual_search(context: BrowserContext) -> bool:
    """
    Ejecuta UNA búsqueda por imagen en Bing para mantener la racha visual.
    Devuelve True si la búsqueda visual se disparó correctamente.
    """
    page = context.pages[0] if context.pages else await context.new_page()

    if not await safe_goto(page, _IMAGES_URL, attempts=4, timeout=25_000):
        log.error("no se pudo cargar bing.com/images")
        return False
    await sleep_jitter(2.0, 4.0)

    # Banner de cookies (EU) — mismo selector compuesto que en searches.py.
    try:
        btn = await page.wait_for_selector(selectors.get("bing.cookies_accept"), timeout=2500)
        if btn:
            await btn.click()
            await sleep_jitter(0.6, 1.4)
    except Exception:
        pass

    # Warm-up de ratón + lectura, como un usuario que va a subir una foto.
    if random.random() < 0.7:
        await random_mouse_drift(page, moves=random.randint(1, 2))
    await sleep_jitter(1.0, 2.5)

    img_path, is_temp = _image_path()
    try:
        file_input = await _find_file_input(page)
        if file_input is None:
            log.warning("no se encontró el input file de búsqueda visual — abortando")
            return False

        # Subir la imagen: dispara el flujo SBI de Bing (POST /images/kblob) y
        # navega a la SERP de la búsqueda visual.
        await file_input.set_input_files(img_path)
        log.info("imagen subida a la búsqueda visual de Bing")
        await sleep_jitter(2.5, 5.0)

        if not await _confirm_visual_results(page):
            log.warning("tras subir la imagen no se llegó a la SERP de búsqueda visual")
            return False

        # Comportarse como un humano leyendo los resultados.
        await human_read(page, 4.0, 9.0)
        await human_scroll(page, random.randint(300, 800))
        await sleep_jitter(2.0, 4.0)
        log.info("búsqueda por imagen completada")
        return True
    except Exception as exc:
        log.error("búsqueda por imagen falló: %s", exc)
        return False
    finally:
        if is_temp and img_path:
            try:
                os.remove(img_path)
            except Exception:
                pass
