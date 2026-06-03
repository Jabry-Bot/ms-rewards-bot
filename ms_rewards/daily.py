"""
Resuelve el Daily Set y promociones del dashboard de Microsoft Rewards.

Estrategia:
  - Ir a https://rewards.bing.com/
  - Buscar cards no completadas (las tienen un check; las pendientes no)
  - Para cada una, click -> se abre una pestaña nueva
  - Dentro de esa pestaña, según el tipo:
      * Artículo / "ver tres anuncios": esperar y scrollear
      * Quiz de selección múltiple: pulsar todas las opciones hasta que dé OK
      * Poll: pulsar una opción aleatoria
      * "This or That": pulsar una opción al azar varias veces
  - Cerrar pestaña y pasar a la siguiente
"""
from __future__ import annotations

import asyncio
import logging
import random

from patchright.async_api import BrowserContext, Page

import config
import selectors
from humanize import bezier_mouse_move, human_read, human_scroll, safe_goto, sleep_jitter

log = logging.getLogger("daily")

REWARDS_URL = "https://rewards.microsoft.com/"

# Estado de fallos de selectores recogidos durante esta ejecución. El run.py
# lo lee al final y, si el modo maintainer está activo, dispara healer.heal.
# Lista de dicts: {"key": "dashboard.card_link", "url": "...", "html": "..."}
broken_selectors: list[dict] = []


def _record_broken(key: str, page_url: str, html: str) -> None:
    broken_selectors.append({"key": key, "url": page_url, "html": html})
    log.warning("selector roto detectado: %s en %s", key, page_url)


async def _click_claim_buttons(page: Page) -> int:
    """
    Escanea la página actual buscando botones de "Reclamar" / "Claim" /
    "Conseguir oferta" y los pulsa uno a uno. Devuelve cuántos pulsó.

    Estrategia doble (robusta a cambios de clase CSS):
      1) selector CSS desde selectors.json (data-bi-name*='claim', etc.)
      2) fallback por texto del elemento (a/button cuyo innerText case-
         insensitive empiece por una keyword localizada).
    """
    css_sel = selectors.get("claim.anywhere", "")
    kw_es = selectors.get("claim.text_keywords_es", "")
    kw_en = selectors.get("claim.text_keywords_en", "")
    keywords = [k.strip().lower() for k in (kw_es + "," + kw_en).split(",") if k.strip()]

    js = """
([cssSel, keywords]) => {
  const out = [];
  const seen = new WeakSet();
  // 1) por CSS
  if (cssSel) {
    document.querySelectorAll(cssSel).forEach(el => {
      if (seen.has(el)) return;
      seen.add(el);
      out.push(el);
    });
  }
  // 2) por texto: a, button, [role=button]
  const candidates = document.querySelectorAll('a, button, [role="button"]');
  candidates.forEach(el => {
    if (seen.has(el)) return;
    const txt = (el.innerText || el.textContent || '').trim().toLowerCase();
    if (!txt || txt.length > 40) return;
    if (keywords.some(k => txt === k || txt.startsWith(k + ' ') || txt === k.replace(/\\s+/g,'')) ) {
      seen.add(el);
      out.push(el);
    }
  });
  // filtrar a visibles y no completados
  const visible = out.filter(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    const cls = (el.className || '').toString().toLowerCase();
    if (cls.includes('complete') || cls.includes('claimed') || cls.includes('disabled')) return false;
    if (el.getAttribute('aria-disabled') === 'true') return false;
    return true;
  });
  // marcar con un id temporal para pulsarlos desde fuera
  visible.forEach((el, i) => { el.setAttribute('data-msr-claim', String(i)); });
  return visible.length;
}
"""
    try:
        count = await page.evaluate(js, [css_sel, keywords])
    except Exception as exc:
        log.debug("escaneo de claim falló: %s", exc)
        return 0

    if not count:
        return 0

    log.info("encontrados %d botones de reclamar", count)
    clicked = 0
    for i in range(count):
        try:
            btn = await page.query_selector(f"[data-msr-claim='{i}']")
            if not btn:
                continue
            if not await btn.is_visible():
                continue
            await btn.scroll_into_view_if_needed(timeout=3000)
            await sleep_jitter(0.6, 1.8)
            # Click puede abrir nueva pestaña: usamos expect_page con timeout corto
            try:
                async with page.context.expect_page(timeout=2500) as new_page_info:
                    await btn.click(timeout=4000)
                opened = await new_page_info.value
                # Es una "card promo" que se abrió: dale unos segundos y ciérrala
                try:
                    await opened.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                await sleep_jitter(3.0, 6.0)
                try:
                    await opened.close()
                except Exception:
                    pass
            except Exception:
                # Click sin pestaña nueva: la página actual procesó el claim
                await sleep_jitter(1.5, 3.5)
            clicked += 1
        except Exception as exc:
            log.debug("click claim %d falló: %s", i, exc)
            continue

    # Limpia los marcadores
    try:
        await page.evaluate(
            "() => document.querySelectorAll('[data-msr-claim]').forEach(e => e.removeAttribute('data-msr-claim'))"
        )
    except Exception:
        pass

    log.info("reclamados: %d", clicked)
    return clicked


async def _list_pending_cards(page: Page) -> list[dict]:
    """
    Inspecciona el DOM del dashboard y devuelve metadata de las cards aún
    no completadas: { href, title, index }.
    """
    # Selectores parametrizados desde selectors.json para que el healer pueda
    # actualizarlos sin tocar este código.
    container_sel = selectors.get("dashboard.card_container")
    badge_sel = selectors.get("dashboard.complete_badge")
    js = """
([containerSel, badgeSel]) => {
  const out = [];
  const cards = document.querySelectorAll(containerSel);
  cards.forEach((card, i) => {
    const completed = card.querySelector(badgeSel);
    const isPunch = card.tagName.toLowerCase() === 'mee-rewards-punch-card';
    if (completed && !isPunch) return;
    const link = card.querySelector('a[href], a[data-bi-id]');
    if (!link) return;
    const href = link.getAttribute('href') || '';
    const title = (card.innerText || '').slice(0, 80).replace(/\\s+/g, ' ').trim();
    out.push({ href, title, index: i, isPunch });
  });
  return out;
}
"""
    try:
        return await page.evaluate(js, [container_sel, badge_sel])
    except Exception as exc:
        log.warning("listado de cards falló: %s", exc)
        return []


async def _solve_quiz_like(page: Page) -> None:
    """
    Resuelve cards interactivas (quiz/poll/this-or-that).
    Estrategia: clickar todas las opciones disponibles secuencialmente hasta
    que ya no quede ninguna pulsable. Funciona para los 3 formatos porque
    cada uno bloquea las opciones después de responder.
    """
    # Selectores comunes para botones de respuesta (parametrizados)
    option_selectors = [
        selectors.get("quiz.this_or_that"),
        selectors.get("quiz.options"),
        selectors.get("quiz.poll"),
    ]

    rounds = 0
    while rounds < 12:  # safety cap (10 preguntas máx en MS Rewards)
        options = []
        for sel in option_selectors:
            try:
                options = await page.query_selector_all(sel)
            except Exception:
                options = []
            if options:
                break

        if not options:
            log.debug("no se encontraron opciones; asumiendo terminado")
            break

        # Filtrar opciones ya respondidas/deshabilitadas
        live = []
        for opt in options:
            try:
                is_visible = await opt.is_visible()
                disabled = await opt.get_attribute("disabled")
                aria_dis = await opt.get_attribute("aria-disabled")
                if is_visible and not disabled and aria_dis != "true":
                    live.append(opt)
            except Exception:
                continue

        if not live:
            log.debug("no quedan opciones vivas")
            break

        # Pausa "leyendo la pregunta"
        await sleep_jitter(*config.DELAYS["before_pick"])

        target = random.choice(live)
        try:
            box = await target.bounding_box()
            if box:
                vp = page.viewport_size or {"width": 1280, "height": 720}
                await bezier_mouse_move(
                    page,
                    random.randint(80, vp["width"] - 80),
                    random.randint(80, vp["height"] - 80),
                    int(box["x"] + box["width"] / 2),
                    int(box["y"] + box["height"] / 2),
                    steps=random.randint(15, 24),
                )
            await target.click()
        except Exception as exc:
            log.debug("click opción falló: %s", exc)
            break

        rounds += 1
        await sleep_jitter(2.5, 5.0)

        # Algunos quizzes muestran "Next" entre preguntas
        next_sel = selectors.get("quiz.next")
        try:
            nxt = await page.query_selector(next_sel)
            if nxt and await nxt.is_visible():
                await nxt.click()
                await sleep_jitter(1.5, 3.0)
        except Exception:
            pass

    log.info("quiz/poll terminado tras %d rondas", rounds)


async def _count_punch_pieces(page: Page) -> int:
    """Devuelve cuántas sub-piezas pendientes hay en la pestaña actual."""
    try:
        elements = await page.query_selector_all(selectors.get("punch_card.piece"))
    except Exception:
        return 0
    live = 0
    for el in elements:
        try:
            if not await el.is_visible():
                continue
            # Excluir las que ya están marcadas como completas
            cls = (await el.get_attribute("class")) or ""
            if "complete" in cls.lower() or "done" in cls.lower():
                continue
            # En algunas versiones, el ancestro tiene la clase 'complete'
            parent_complete = await el.evaluate(
                "(el) => !!el.closest('.complete, [class*=\"completed\"]')"
            )
            if parent_complete:
                continue
            live += 1
        except Exception:
            continue
    return live


async def _handle_punch_card_hub(hub: Page, context: BrowserContext) -> int:
    """
    El hub muestra todas las "piezas" del puzzle semanal. Cada pieza es un
    link que abre su propia pestaña con una sub-actividad (artículo o quiz).
    Recorremos las que quedan pendientes en bucle hasta agotarlas.
    """
    pieces_done = 0
    safety = 0
    while safety < 12:  # máx 12 piezas (las punch cards llegan hasta ~10)
        safety += 1

        # Recargar para que se vea el estado real tras la pieza anterior
        if pieces_done > 0:
            try:
                await hub.reload(wait_until="domcontentloaded", timeout=15_000)
            except Exception:
                pass
            await sleep_jitter(2.0, 4.0)

        pending = await _count_punch_pieces(hub)
        log.info("punch card: %d piezas pendientes", pending)
        if pending == 0:
            break

        # Localizar el primer link pendiente y clickarlo
        try:
            pieces = await hub.query_selector_all(selectors.get("punch_card.piece"))
        except Exception:
            pieces = []

        target = None
        for el in pieces:
            try:
                if not await el.is_visible():
                    continue
                cls = (await el.get_attribute("class")) or ""
                if "complete" in cls.lower():
                    continue
                parent_complete = await el.evaluate(
                    "(el) => !!el.closest('.complete, [class*=\"completed\"]')"
                )
                if parent_complete:
                    continue
                target = el
                break
            except Exception:
                continue

        if target is None:
            log.info("no se encontró pieza clickable; saliendo del hub")
            break

        # Click → abre nueva pestaña con la sub-actividad
        sub_page: Page | None = None
        try:
            box = await target.bounding_box()
            if box:
                vp = hub.viewport_size or {"width": 1280, "height": 720}
                await bezier_mouse_move(
                    hub,
                    random.randint(80, vp["width"] - 80),
                    random.randint(80, vp["height"] - 80),
                    int(box["x"] + box["width"] / 2),
                    int(box["y"] + box["height"] / 2),
                    steps=random.randint(15, 25),
                )
            async with context.expect_page(timeout=10_000) as sub_info:
                await target.click()
            sub_page = await sub_info.value
        except Exception as exc:
            log.warning("no se abrió la sub-pieza: %s", exc)
            # Algunas piezas navegan en la misma pestaña — intentamos resolver
            # directamente sobre el hub.
            try:
                await _handle_activity_tab(hub)
                pieces_done += 1
            except Exception:
                pass
            await sleep_jitter(*config.DELAYS["between_cards"])
            continue

        try:
            await _handle_activity_tab(sub_page)
            pieces_done += 1
        except Exception as exc:
            log.warning("pieza falló: %s", exc)
        finally:
            try:
                await sub_page.close()
            except Exception:
                pass

        await sleep_jitter(*config.DELAYS["between_cards"])

    # Tras terminar las piezas, algunas punch cards tienen un botón final
    # de "Reclamar / Claim" para la recompensa total. Lo buscamos en el hub.
    try:
        claimed = await _click_claim_buttons(hub)
        if claimed:
            log.info("punch card: %d recompensa(s) finales reclamadas", claimed)
    except Exception as exc:
        log.debug("no se pudo reclamar punch card final: %s", exc)

    log.info("punch card terminada: %d piezas completadas", pieces_done)
    return pieces_done


async def _handle_activity_tab(page: Page) -> None:
    """Resuelve una pestaña que contiene UNA actividad (no un hub)."""
    is_interactive = False
    try:
        if await page.query_selector(selectors.get("quiz.interactive_indicators")):
            is_interactive = True
    except Exception:
        pass

    if is_interactive:
        log.info("actividad interactiva (quiz/poll)")
        await _solve_quiz_like(page)
    else:
        log.info("actividad de lectura/visita")
        await human_read(page, *config.DELAYS["on_card"])
        await human_scroll(page, random.randint(400, 900))
        await sleep_jitter(2.0, 4.0)


async def _handle_card_tab(page: Page, context: BrowserContext) -> None:
    """
    Detecta si la pestaña abierta es un hub de punch card o una actividad
    suelta, y la resuelve.
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:
        pass
    await sleep_jitter(2.0, 4.5)

    # ¿Hub de punch card? Si vemos 2+ sub-piezas pendientes, lo tratamos así.
    pieces = await _count_punch_pieces(page)
    if pieces >= 2:
        log.info("hub de punch card detectado (%d piezas)", pieces)
        await _handle_punch_card_hub(page, context)
        return

    await _handle_activity_tab(page)


async def run_daily(context: BrowserContext) -> int:
    """Ejecuta todas las cards pendientes del dashboard. Devuelve cuántas hizo."""
    pages = context.pages
    dashboard = pages[0] if pages else await context.new_page()

    # Primera navegación tras abrir Chrome → reintentos generosos: el resolver
    # de red puede tardar un instante en estar listo (ERR_NAME_NOT_RESOLVED).
    if not await safe_goto(dashboard, REWARDS_URL, attempts=6, timeout=25_000):
        log.error("no se pudo cargar el dashboard tras varios intentos")
        return 0
    await sleep_jitter(3.0, 6.0)
    await human_scroll(dashboard, random.randint(200, 500))
    await sleep_jitter(1.0, 2.5)

    cards = await _list_pending_cards(dashboard)
    log.info("cards pendientes: %d", len(cards))
    if not cards:
        # Si el dashboard cargó OK (URL contiene rewards) pero no hay cards,
        # probablemente los selectores del container están rotos. Guardamos
        # el HTML para que el healer lo procese tras la ejecución.
        try:
            url_ok = "rewards." in dashboard.url
            html = await dashboard.content() if url_ok else ""
            if url_ok and len(html) > 5000:
                _record_broken("dashboard.card_container", dashboard.url, html)
        except Exception:
            pass
        return 0

    done = 0
    simple_click_done = 0
    for c in cards:
        log.info("→ abriendo card: %s", (c.get("title") or "")[:60])

        # Hay tres tipos de cards en MS Rewards:
        #   A) Card que abre PESTAÑA NUEVA con quiz/poll/artículo → flujo normal.
        #   B) Card que NAVEGA EN LA MISMA PESTAÑA al destino → poco frecuente.
        #   C) Card de "SIMPLE CLICK ACREDITA": el click acredita puntos
        #      directamente sin abrir pestaña ni navegar (p.ej. "Reclama X
        #      puntos por activar la barra", "Comparte con un amigo", banners
        #      promo). Antes las contábamos como fallo porque expect_page daba
        #      timeout — pero los puntos sí se acreditaban.
        #
        # Distinguimos comparando la cantidad de pestañas / la URL antes y
        # después del click.
        _CLICK_JS = """([idx, containerSel]) => {
            const cards = document.querySelectorAll(containerSel);
            const card = cards[idx];
            if (!card) return false;
            const link = card.querySelector('a[href], a[data-bi-id]');
            if (!link) return false;
            link.scrollIntoView({block: 'center'});
            link.click();
            return true;
        }"""
        container_sel = selectors.get("dashboard.card_container")
        pages_before = len(context.pages)
        url_before = dashboard.url

        # expect_page con timeout corto: si tras 3.5s no se abrió pestaña,
        # asumimos card tipo B/C y seguimos. Si se abre, la procesamos.
        new_page: Page | None = None
        try:
            async with context.expect_page(timeout=3_500) as new_page_info:
                clicked = await dashboard.evaluate(_CLICK_JS, [c["index"], container_sel])
                if not clicked:
                    raise RuntimeError(f"card idx={c['index']} sin link clickable")
            new_page = await new_page_info.value
        except Exception as exc:
            msg = str(exc)
            if "sin link clickable" in msg:
                # Card no tenía link real (raro tras filtros). Saltamos.
                log.debug("card sin link clickable, saltando: %s", c.get("title", "")[:40])
                continue
            # Click hecho pero ninguna pestaña nueva. Determinamos qué pasó:
            await sleep_jitter(0.8, 1.6)
            pages_after = len(context.pages)
            url_after = dashboard.url
            if pages_after > pages_before:
                # Race: la pestaña sí se abrió pero el expect_page expiró
                # justo antes. Recogerla manualmente.
                new_page = context.pages[-1]
            elif url_after != url_before:
                # Tipo B: la card navegó en la misma pestaña.
                log.info("card navegó en la misma pestaña; procesando como actividad")
                try:
                    await _handle_card_tab(dashboard, context)
                    done += 1
                except Exception as exc2:
                    log.warning("card en misma pestaña falló: %s", exc2)
                # Volver al dashboard
                try:
                    await safe_goto(dashboard, REWARDS_URL, attempts=3, timeout=20_000)
                    await sleep_jitter(2.0, 4.0)
                except Exception:
                    pass
                continue
            else:
                # Tipo C: simple click — los puntos se acreditan sin más.
                # Lo contamos como completada (era el bug: antes la perdíamos).
                log.info("card de simple-click: puntos acreditados sin pestaña")
                simple_click_done += 1
                # Pequeña pausa entre cards para no abrumar al dashboard
                await sleep_jitter(*config.DELAYS["between_cards"])
                continue

        if new_page is None:
            continue

        try:
            await _handle_card_tab(new_page, context)
            done += 1
        except Exception as exc:
            log.warning("card falló: %s", exc)
        finally:
            try:
                await new_page.close()
            except Exception:
                pass

        # Pausa entre cards
        await sleep_jitter(*config.DELAYS["between_cards"])

    # Volver al dashboard y refrescar para que se actualicen los checks
    try:
        await dashboard.reload(wait_until="domcontentloaded", timeout=15_000)
        await sleep_jitter(3.0, 6.0)
    except Exception:
        pass

    # Reclamar cualquier ficha pendiente que haya aparecido tras completar
    # las cards: bonus de daily set, streak, punch card final, etc.
    try:
        claimed = await _click_claim_buttons(dashboard)
        if claimed:
            log.info("reclamadas %d fichas en el dashboard", claimed)
            # Refrescar para confirmar
            await dashboard.reload(wait_until="domcontentloaded", timeout=15_000)
            await sleep_jitter(2.0, 4.0)
    except Exception as exc:
        log.warning("reclamación en dashboard falló: %s", exc)

    total = done + simple_click_done
    if simple_click_done:
        log.info(
            "daily set: %d cards completadas (%d con sub-actividad + %d de simple-click)",
            total, done, simple_click_done,
        )
    else:
        log.info("daily set: %d cards completadas", total)
    return total
