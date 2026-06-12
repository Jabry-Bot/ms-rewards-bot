"""
Desinstalador NO interactivo del bot de Microsoft Rewards.

Cada limpieza se activa con su propio flag de argparse y es independiente de
las demás: si una falla, las restantes siguen ejecutándose. Si no se pasa
ningún flag, no hace nada y avisa.

Flags disponibles:
  --task         Quita la Scheduled Task (winutil, Python puro).
  --state        Borra el estado de ejecución (state/last_run.json).
  --credentials  Borra las credenciales cifradas (state/credentials.bin).
  --profile      Cierra el navegador del bot y borra su perfil (sesión iniciada).
  --env          Quita las variables de entorno persistentes del usuario (MSR_*).

Pensado para lanzarse desde el panel (panel/app.py vía core.build_uninstall_command),
que vuelca esta salida en su log en vivo. Por eso imprime el progreso de cada
paso de forma legible.

NO borra el .venv: no se puede borrar de forma fiable el intérprete que está
ejecutando este propio script. Para eliminar TODO, borra la carpeta del bot a mano.
"""
from __future__ import annotations

import argparse
import logging
import sys

import config
import credentials
import switch_account as switch
import winutil

log = logging.getLogger("uninstall")


def _remove_task() -> None:
    """Quita la Scheduled Task (winutil, Python puro)."""
    print("[*] Quitando tarea programada...")
    if winutil.uninstall_task():
        print("  OK, tarea eliminada.")
    else:
        print("  No estaba registrada. Nada que hacer.")


def _remove_state() -> None:
    """Borra state/last_run.json si existe."""
    print("[*] Borrando estado de ejecución (last_run.json)...")
    p = config.LAST_RUN_PATH
    if p.exists():
        p.unlink()
        print("  OK.")
    else:
        print("  No existía. Nada que hacer.")


def _remove_credentials() -> None:
    """Borra las credenciales cifradas (state/credentials.bin)."""
    print("[*] Borrando credenciales cifradas...")
    if config.CREDENTIALS_PATH.exists():
        credentials.delete()
        print("  OK.")
    else:
        print("  No existían. Nada que hacer.")


def _remove_profile() -> None:
    """Cierra el navegador del bot y borra su perfil (kill + wait + wipe robusto)."""
    print("[*] Cerrando navegador del bot y borrando el perfil...")
    if not switch._kill_and_wait():
        print(
            "  AVISO: aún hay navegador del bot abierto. Ciérralo manualmente y "
            "reintenta esta opción."
        )
        return
    print("  Navegador cerrado.")
    if switch._wipe_profile():
        print("  OK, perfil eliminado.")
    else:
        print(
            "  ERROR: no se pudo borrar el perfil por completo (archivo bloqueado). "
            "Cierra TODO el navegador y reintenta."
        )


def _remove_env() -> None:
    """Quita las variables de entorno persistentes del usuario (MSR_*)."""
    print("[*] Quitando variables de entorno persistentes (MSR_*)...")
    removed = winutil.delete_env_vars()
    print(f"  OK, {removed} variable(s) MSR_* eliminadas.")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(
        description="Desinstalador no interactivo del bot de Microsoft Rewards.",
    )
    parser.add_argument("--task", action="store_true",
                        help="Quita la Scheduled Task.")
    parser.add_argument("--state", action="store_true",
                        help="Borra el estado de ejecución (last_run.json).")
    parser.add_argument("--credentials", action="store_true",
                        help="Borra las credenciales cifradas.")
    parser.add_argument("--profile", action="store_true",
                        help="Cierra el navegador del bot y borra su perfil.")
    parser.add_argument("--env", action="store_true",
                        help="Quita las variables de entorno persistentes (MSR_*).")
    args = parser.parse_args()

    print("=" * 60)
    print(" Desinstalar - ms_rewards")
    print("=" * 60)

    if not any((args.task, args.state, args.credentials, args.profile, args.env)):
        print("\nNo se seleccionó ninguna opción de desinstalación. Nada que hacer.")
        print("Pasa al menos un flag: --task --state --credentials --profile --env")
        return 0

    # Cada limpieza en su propio try: que un fallo no aborte las demás.
    pasos = [
        (args.task, _remove_task),
        (args.state, _remove_state),
        (args.credentials, _remove_credentials),
        (args.profile, _remove_profile),
        (args.env, _remove_env),
    ]
    for activo, accion in pasos:
        if not activo:
            continue
        try:
            accion()
        except Exception as exc:
            log.exception("paso %s falló: %s", accion.__name__, exc)
            print(f"  ERROR en este paso: {exc}")

    print("\n" + "=" * 60)
    print(" Desinstalación finalizada.")
    print(" Para eliminar TODO, borra la carpeta del bot manualmente.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
