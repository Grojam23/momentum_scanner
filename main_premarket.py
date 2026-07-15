"""
Punto de entrada para el escaneo de PREMARKET, con sus propios criterios
(config_premarket.yaml): solo % de subida, market cap, rango de precio y
acciones disponibles — sin rotación de float ni exigencia de noticia.

Reutiliza EXACTAMENTE la misma lógica de main.py (get_universe, get_quotes,
evaluate_candidates, envío de correo, etc.) — la única diferencia es qué
archivo de configuración carga. Así no hay reglas duplicadas en dos lugares
distintos, solo dos "perfiles" de criterios.

Uso:
    python main_premarket.py             # corre una vez
    python main_premarket.py --debug MIMI  # diagnóstico de un ticker con
                                             # los criterios de premarket
"""

import sys
import time

from main import load_config, run_once, debug_ticker, log

CONFIG_PATH = "config_premarket.yaml"


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--debug":
        cfg = load_config(CONFIG_PATH)
        debug_ticker(sys.argv[2], cfg)
        return

    cfg = load_config(CONFIG_PATH)
    mode = cfg["schedule"].get("run_mode", "once")

    if mode == "once":
        run_once(cfg)
    elif mode == "loop":
        interval = cfg["schedule"].get("interval_minutes", 15)
        log.info(f"[PREMARKET] Modo loop activado. Corriendo cada {interval} minutos. Ctrl+C para detener.")
        while True:
            try:
                run_once(cfg)
            except Exception as e:
                log.error(f"Error en el ciclo: {e}")
            time.sleep(interval * 60)
    else:
        log.error(f"run_mode desconocido: {mode}. Usa 'once' o 'loop'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
