"""
Genera la misma frase con varias voces para elegir la más dulce.
Los archivos quedan en salida/muestras/ (mp3).
"""
import asyncio
from pathlib import Path

import edge_tts

FRASE = ("Había una vez un pequeño león llamado Lolo, que vivía junto a su amiga Lilu, "
         "la exploradora. Cierra los ojitos, respira despacito, y escucha esta historia.")

# (nombre del archivo, voz, velocidad, tono)
VARIANTES = [
    ("01_elena_ar_suave",   "es-AR-ElenaNeural",   "-15%", "+0Hz"),
    ("02_elena_ar_dulce",   "es-AR-ElenaNeural",   "-20%", "+6Hz"),
    ("03_dalia_mx_suave",   "es-MX-DaliaNeural",   "-15%", "+0Hz"),
    ("04_dalia_mx_dulce",   "es-MX-DaliaNeural",   "-20%", "+6Hz"),
    ("05_paloma_us",        "es-US-PalomaNeural",  "-15%", "+0Hz"),
    ("06_salome_co",        "es-CO-SalomeNeural",  "-15%", "+0Hz"),
    ("07_catalina_cl",      "es-CL-CatalinaNeural", "-15%", "+0Hz"),
    ("08_valentina_uy",     "es-UY-ValentinaNeural", "-15%", "+0Hz"),
    ("09_elvira_es",        "es-ES-ElviraNeural",  "-15%", "+0Hz"),
    ("10_tomas_ar_hombre",  "es-AR-TomasNeural",   "-15%", "+0Hz"),
]


async def main():
    out = Path("salida/muestras")
    out.mkdir(parents=True, exist_ok=True)
    for nombre, voz, vel, tono in VARIANTES:
        try:
            await edge_tts.Communicate(FRASE, voz, rate=vel, pitch=tono).save(str(out / f"{nombre}.mp3"))
            print("OK", nombre)
        except Exception as e:  # una voz que falle no frena las demás
            print("FALLÓ", nombre, e)


asyncio.run(main())
