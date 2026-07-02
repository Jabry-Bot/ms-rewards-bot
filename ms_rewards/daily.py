"""
Resuelve el Daily Set y promociones de Microsoft Rewards — modo API-driven.

Contexto (rediseño 2026-07): el dashboard pasó a Next.js/Tailwind y las cards ya
no tienen selectores CSS estables (`mee-rewards-*` desaparecieron). En vez de
scrapear el DOM, ahora leemos las actividades pendientes de la API interna
(dashboard_api.fetch_dashboard → pending_activities) y completamos cada una
VISITANDO su `destinationUrl`:

  - actividad de búsqueda (q=...): basta cargar la SERP; se acredita sola.
  - quiz / poll / this-or-that: la página de bing.com sí conserva sus selectores
    (#rqAnswerOption0, .rqOptionWrap, ...), así que _handle_activity_tab detecta
    el tipo y _solve_quiz_like lo resuelve como antes.

Los helpers de resolución de actividad (_handle_activity_tab, _solve_quiz_like) y
de reclamar bonus (_click_claim_buttons) operan sobre páginas bing.com, no sobre
el dashboard rediseñado, por lo que siguen siendo válidos.
"""
from __future__ import annotations

import logging
import random

from patchright.async_api import BrowserContext, Page

import config
import dashboard_api
import selectors
from humanize import bezier_mouse_move, human_read, human_scroll, safe_goto, sleep_jitter

log = logging.getLogger("daily")

# Dashboard canónico tras el rediseño (rewards.microsoft.com redirige aquí).
REWARDS_URL = "https://rewards.bing.com/dashboard"

# Estado de fallos recogidos durante esta ejecución. run.py lo lee al final y,
# si el modo maintainer está activo, dispara healer.heal. Con el modelo
# API-driven la única "rotura" posible del daily set es que la API no responda.
# Lista de dicts: {"key": "dashboard.api", "url": "...", "html": "..."}
broken_selectors: list[dict] = []


def _record_broken(key: str, page_url: str, html: str) -> None:
    broken_selectors.append({"key": key, "url": page_url, "html": html})
    log.warning("fallo detectado: %s en %s", key, page_url)


async def _click_claim_buttons(page: Page) -> int:
    """
    Escanea la página actual buscando botones de "Reclamar" / "Claim" /
    "Solicitar" / "Conseguir oferta" y los pulsa uno a uno (las ofertas con
    botón "Solicitar" acreditan puntos extra). Devuelve cuántos pulsó.

    Estrategia doble (robusta a cambios de clase CSS):
      1) selector CSS desde selectors.json (data-bi-name*='claim', etc.)
      2) fallback por texto del elemento (a/button cuyo innerText case-
         insensitive empiece por una keyword localizada). El fallback por texto
         sigue funcionando en el dashboard rediseñado, donde no hay CSS estable.
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


async def _handle_activity_tab(page: Page) -> None:
    """Resuelve una pestaña que contiene UNA actividad (búsqueda / quiz / lectura)."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:
        pass
    await sleep_jitter(2.0, 4.5)

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


# Localiza en el dashboard el <a> cuyo href es el destino de la actividad. El
# href del daily set contiene el offerId (filtro BTDSUOID), así que es un ancla
# fiable; para more/punch casamos por href exacto o por el código form=.
_TAG_ANCHOR_JS = r"""
([url, offerId]) => {
  // Parámetro de búsqueda q (case-insensitive) — discriminante estable entre
  // cards, robusto a la normalización de comillas/espacios del navegador.
  const getQ = (href) => {
    try {
      const sp = new URL(href, location.origin).searchParams;
      for (const [k, v] of sp) { if (k.toLowerCase() === 'q') return v; }
    } catch (e) {}
    return null;
  };
  const targetQ = getQ(url);
  const anchors = [...document.querySelectorAll('a[href]')];
  let m = null;
  // 1) por offerId en el href (daily set con BTDSUOID)
  if (offerId) m = anchors.find(a => a.href.includes(offerId));
  // 2) por el parámetro de búsqueda q (Tetons y otras sin offerId en el href)
  if (!m && targetQ) m = anchors.find(a => getQ(a.href) === targetQ);
  // 3) por href normalizado exacto
  if (!m) { try { const nu = new URL(url, location.origin).href; m = anchors.find(a => a.href === nu); } catch (e) {} }
  if (!m) return false;
  document.querySelectorAll('[data-msr-act]').forEach(e => e.removeAttribute('data-msr-act'));
  m.setAttribute('data-msr-act', '1');
  m.scrollIntoView({ block: 'center' });
  return true;
}
"""


async def _open_activity_via_card(page: Page, context: BrowserContext, activity) -> Page | None:
    """
    Localiza el <a> de la actividad en el dashboard y lo CLICA con un clic
    confiado de Playwright. Clave del rediseño: Bing solo acredita el offer
    cuando la navegación nace de ese clic real desde el dashboard — un goto
    directo al destinationUrl dispara el beacon reportActivity pero NO acredita.
    Devuelve la pestaña abierta, o None si no encontró la card / no abrió tab.
    """
    # Reintentos: la SPA puede tardar en pintar el <a> de la card.
    tagged = False
    for _ in range(3):
        try:
            tagged = await page.evaluate(
                _TAG_ANCHOR_JS, [activity.destination_url, activity.offer_id])
        except Exception as exc:
            log.debug("búsqueda de card falló: %s", exc)
            tagged = False
        if tagged:
            break
        await sleep_jitter(1.0, 1.5)
    if not tagged:
        return None
    try:
        async with context.expect_page(timeout=8000) as info:
            await page.click('[data-msr-act="1"]', timeout=8000)
        return await info.value
    except Exception as exc:
        log.debug("clic en card no abrió pestaña: %s", exc)
        return None


async def _wait_for_activity_anchors(page: Page, timeout: float = 15.0) -> bool:
    """
    Espera a que la SPA del dashboard pinte los <a target=_blank> de las cards
    del daily set (aparecen tras la hidratación + fetch de la API). Sondea el
    DOM en intervalos cortos. Devuelve True si aparecieron.
    """
    probe = ("() => document.querySelectorAll("
             "\"a[href*='rnoreward'], a[href*='DailySet'], a[href*='dsetqu'], a[href*='form=ML']\""
             ").length")
    attempts = max(1, int(timeout))
    for _ in range(attempts):
        try:
            if await page.evaluate(probe):
                return True
        except Exception:
            pass
        await sleep_jitter(0.8, 1.2)
    return False


async def run_daily(context: BrowserContext) -> int:
    """
    Ejecuta las actividades pendientes del dashboard leídas de la API interna
    (daily set + more promotions + punch cards). Cada actividad `urlreward` se
    completa visitando su `destinationUrl`. Devuelve cuántas se ejecutaron.
    """
    page = context.pages[0] if context.pages else await context.new_page()

    # Primera navegación tras abrir Chrome → reintentos generosos: el resolver
    # de red puede tardar un instante en estar listo (ERR_NAME_NOT_RESOLVED).
    if not await safe_goto(page, REWARDS_URL, attempts=6, timeout=25_000):
        log.error("no se pudo cargar el dashboard tras varios intentos")
        return 0
    await sleep_jitter(3.0, 6.0)

    dashboard = await dashboard_api.fetch_dashboard(context, navigate=False)
    if not dashboard:
        # La API no respondió pese a haber sesión activa: posible cambio de la
        # API o rediseño más profundo. Guardamos el HTML para diagnóstico.
        try:
            html = await page.content()
        except Exception:
            html = ""
        _record_broken("dashboard.api", page.url, html)
        log.error("/api/getuserinfo no devolvió datos; nada que hacer en daily set")
        return 0

    activities = dashboard_api.pending_activities(dashboard)
    log.info("actividades pendientes (API): %d", len(activities))
    if not activities:
        # Nada pendiente; intenta reclamar cualquier bonus/streak visible.
        try:
            await _click_claim_buttons(page)
        except Exception:
            pass
        return 0

    # Esperar a que la SPA pinte los <a> de las cards antes de clicarlas.
    await _wait_for_activity_anchors(page)

    done = 0
    for act in activities:
        log.info("→ %s [%s] (%d pts)", act.title[:60], act.source, act.points_max)
        sub: Page | None = None
        via = "card"
        try:
            # Vía principal: clic real en la card del dashboard — la única que
            # acredita tras el rediseño (un goto directo no acredita el offer).
            sub = await _open_activity_via_card(page, context, act)
            if sub is None:
                # Fallback best-effort: goto directo. Dispara el beacon; puede
                # no acreditar, pero es mejor que saltar la actividad.
                via = "goto"
                sub = await context.new_page()
                if not await safe_goto(sub, act.destination_url, attempts=3, timeout=25_000):
                    log.warning("no se pudo abrir la actividad: %s", act.title[:40])
                    continue
            # La actividad debe quedar en PRIMER PLANO mientras se resuelve.
            try:
                await sub.bring_to_front()
            except Exception:
                pass
            await _handle_activity_tab(sub)
            done += 1
            log.info("   ejecutada vía %s", via)
        except Exception as exc:
            log.warning("actividad falló (%s): %s", act.title[:40], exc)
        finally:
            if sub is not None:
                try:
                    await sub.close()
                except Exception:
                    pass
            # Volver al dashboard para clicar la siguiente card.
            try:
                await page.bring_to_front()
            except Exception:
                pass
        # Pausa humana entre actividades.
        await sleep_jitter(*config.DELAYS["between_cards"])

    # Volver al dashboard y reclamar bonus de streak/daily que hayan aparecido.
    try:
        await safe_goto(page, REWARDS_URL, attempts=3, timeout=20_000)
        await sleep_jitter(2.0, 4.0)
        claimed = await _click_claim_buttons(page)
        if claimed:
            log.info("reclamadas %d fichas de bonus", claimed)
    except Exception as exc:
        log.debug("reclamación en dashboard falló: %s", exc)

    # Confirmar cuántas quedan pendientes tras la corrida (re-consultando la API).
    try:
        after = await dashboard_api.fetch_dashboard(context, navigate=False)
        remaining = len(dashboard_api.pending_activities(after)) if after else -1
        log.info("daily set: %d actividades ejecutadas; pendientes restantes: %s",
                 done, remaining if remaining >= 0 else "?")
    except Exception:
        pass

    return done
