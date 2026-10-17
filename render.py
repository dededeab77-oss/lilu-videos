"""
Genera un video de cuento infantil a partir de un story.json.

Uso:  python render.py stories/ejemplo/story.json

Por cada escena:  voz (edge-tts)  +  ilustración con zoom lento  +  subtítulos.
Si falta la imagen de una escena, crea una lámina de prueba.
Si hay un archivo de audio en la carpeta musica/, lo mezcla de fondo.
"""
import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 25
PAUSA_FINAL = 0.8  # segundos de silencio al terminar cada escena
FUENTES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(" ".join(cmd))
        print(r.stderr[-2000:])
        sys.exit(f"Falló el comando: {cmd[0]}")
    return r.stdout


def fuente(size):
    for f in FUENTES:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def duracion(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def lamina_prueba(path, numero, titulo):
    """Imagen provisoria hasta que existan las ilustraciones reales."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(250 - 40 * t), int(200 - 50 * t), int(110 + 40 * t)))
    d.text((W // 2, H // 2 - 40), f"Escena {numero}", font=fuente(90), fill=(90, 50, 20), anchor="mm")
    d.text((W // 2, H // 2 + 50), titulo, font=fuente(36), fill=(120, 70, 30), anchor="mm")
    img.save(path)


def subtitulo_png(texto, path):
    """PNG transparente del tamaño del video con el subtítulo abajo."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = fuente(38)
    lineas = textwrap.wrap(texto, width=46)[:4]
    alto_linea = 50
    alto = alto_linea * len(lineas) + 30
    y0 = H - alto - 25
    d.rounded_rectangle([60, y0, W - 60, y0 + alto], radius=22, fill=(0, 0, 0, 150))
    for i, l in enumerate(lineas):
        d.text((W // 2, y0 + 15 + alto_linea * i + alto_linea // 2), l,
               font=f, fill=(255, 255, 255, 255), anchor="mm")
    img.save(path)


async def _tts(texto, voz, velocidad, tono, salida):
    import edge_tts
    await edge_tts.Communicate(texto, voz, rate=velocidad, pitch=tono).save(str(salida))


def generar_voz(texto, voz, velocidad, tono, salida):
    if os.environ.get("FAKE_TTS"):  # solo para pruebas sin internet
        seg = max(2.0, len(texto) / 14)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seg}",
             "-c:a", "libmp3lame", str(salida)])
        return
    asyncio.run(_tts(texto, voz, velocidad, tono, salida))


def clip_escena(imagen, sub, audio, dur, salida):
    frames = int(dur * FPS) + 1
    filtro = (
        f"[0:v]scale=2560:-2,zoompan=z='min(zoom+0.0005,1.12)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}[bg];"
        f"[bg][1:v]overlay=0:0,format=yuv420p[v];"
        f"[2:a]apad=pad_dur={PAUSA_FINAL}[a]"
    )
    run(["ffmpeg", "-y", "-loop", "1", "-i", str(imagen), "-i", str(sub), "-i", str(audio),
         "-filter_complex", filtro, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
         "-c:a", "aac", "-ar", "44100", "-ac", "2", str(salida)])


# ----------------------------------------------------------------------
#  INTRO CANTADA CON LOLO (boca que se mueve al ritmo del audio)
# ----------------------------------------------------------------------
def encajar_imagen(origen, destino):
    """Acomoda la imagen en 1280x720 sin deformarla; los costados se rellenan con el mismo fondo difuminado."""
    from PIL import ImageFilter
    im = Image.open(origen).convert("RGB")
    cubrir = max(W / im.width, H / im.height)
    fondo = im.resize((int(im.width * cubrir) + 1, int(im.height * cubrir) + 1), Image.LANCZOS)
    fondo = fondo.crop(((fondo.width - W) // 2, (fondo.height - H) // 2,
                        (fondo.width - W) // 2 + W, (fondo.height - H) // 2 + H))
    fondo = fondo.filter(ImageFilter.GaussianBlur(45))
    escala = min(W / im.width, H / im.height)
    nuevo = im.resize((int(im.width * escala), int(im.height * escala)), Image.LANCZOS)
    mascara = Image.new("L", nuevo.size, 255).filter(ImageFilter.GaussianBlur(1))
    fondo.paste(nuevo, ((W - nuevo.width) // 2, (H - nuevo.height) // 2))
    fondo.save(destino)


def niveles_audio(audio, ventana=0.08):
    """Volumen (RMS) del audio en tramos cortos, sin usar librerías extra."""
    import array
    import math
    import wave
    wav = Path("tmp") / "intro_mono.wav"
    run(["ffmpeg", "-y", "-i", str(audio), "-ac", "1", "-ar", "8000", str(wav)])
    with wave.open(str(wav)) as w:
        datos = array.array("h", w.readframes(w.getnframes()))
    paso = int(8000 * ventana)
    out = []
    for i in range(0, len(datos), paso):
        tr = datos[i:i + paso]
        if tr:
            out.append(math.sqrt(sum(x * x for x in tr) / len(tr)))
    return out


def hacer_intro(cfg, base, tmp):
    """Devuelve la ruta del clip de intro, o None si faltan archivos."""
    audio = base / cfg.get("audio", "")
    if not audio.is_file():
        print("Intro: no encontré el audio, la salto.")
        return None
    abierta = base / cfg.get("imagen_boca_abierta", "")
    cerrada = base / cfg.get("imagen_boca_cerrada", "")
    if not abierta.is_file():
        print("Intro: falta la imagen de Lolo, la salto.")
        return None

    audio_final = tmp / "intro_audio.wav"
    f = float(cfg.get("ardilla_factor", 1.25))
    if cfg.get("ardilla"):
        # sube el tono (voz de ardillita) sin cambiar la duración
        run(["ffmpeg", "-y", "-i", str(audio), "-ar", "44100", "-ac", "2", "-af",
             f"asetrate=44100*{f},aresample=44100,atempo={1 / f:.4f}", str(audio_final)])
    else:
        run(["ffmpeg", "-y", "-i", str(audio), "-ar", "44100", "-ac", "2", str(audio_final)])
    dur = duracion(audio_final) + 0.5

    fr_abierta = tmp / "intro_abierta.png"
    encajar_imagen(abierta, fr_abierta)
    salida = tmp / "clip_intro.mp4"

    if cerrada.is_file():
        fr_cerrada = tmp / "intro_cerrada.png"
        encajar_imagen(cerrada, fr_cerrada)
        niv = niveles_audio(audio_final)
        orden = sorted(niv)
        ref = orden[int(len(orden) * 0.9)] if orden else 1
        umbral = max(ref * 0.30, 1)
        lineas = []
        for n in niv:
            img = fr_abierta if n > umbral else fr_cerrada
            lineas.append(f"file '{img.name}'\nduration 0.08\n")
        lineas.append(f"file '{fr_cerrada.name}'\n")
        lista = tmp / "lista_intro.txt"
        lista.write_text("".join(lineas), encoding="utf-8")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lista), "-i", str(audio_final),
             "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "22", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-t", f"{dur:.2f}", str(salida)])
    else:
        print("Intro: sin imagen de boca cerrada, queda quieta con zoom suave.")
        sub = tmp / "intro_sub.png"
        Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(sub)
        clip_escena(fr_abierta, sub, audio_final, dur, salida)
    return salida


def main():
    if len(sys.argv) < 2:
        sys.exit("Uso: python render.py stories/ejemplo/story.json")
    story_path = Path(sys.argv[1])
    base = story_path.parent
    story = json.loads(story_path.read_text(encoding="utf-8"))
    voz = story.get("voz", "es-AR-ElenaNeural")
    vel = story.get("velocidad", "-10%")
    tono = story.get("tono", "+0Hz")

    tmp = Path("tmp"); tmp.mkdir(exist_ok=True)
    out_dir = Path("salida"); out_dir.mkdir(exist_ok=True)
    clips = []

    if story.get("intro"):
        intro = hacer_intro(story["intro"], base, tmp)
        if intro:
            clips.append(intro)

    for i, esc in enumerate(story["escenas"], start=1):
        print(f"Escena {i}: {esc['texto'][:50]}...")
        img = base / esc.get("imagen", "")
        if not img.is_file():
            img = tmp / f"prueba_{i}.png"
            lamina_prueba(img, i, story["titulo"])
        audio = tmp / f"voz_{i}.mp3"
        generar_voz(esc["texto"], voz, vel, tono, audio)
        dur = duracion(audio) + PAUSA_FINAL
        sub = tmp / f"sub_{i}.png"
        subtitulo_png(esc["texto"], sub)
        clip = tmp / f"clip_{i}.mp4"
        clip_escena(img, sub, audio, dur, clip)
        clips.append(clip)

    lista = tmp / "lista.txt"
    lista.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    sin_musica = tmp / "sin_musica.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy", str(sin_musica)])

    nombre = story_path.parent.name
    final = out_dir / f"{nombre}.mp4"
    musicas = sorted(Path("musica").glob("*.mp3")) + sorted(Path("musica").glob("*.wav"))
    if musicas:
        print(f"Mezclando música: {musicas[0].name}")
        run(["ffmpeg", "-y", "-i", str(sin_musica), "-stream_loop", "-1", "-i", str(musicas[0]),
             "-filter_complex",
             "[1:a]volume=0.10[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(final)])
    else:
        sin_musica.replace(final)

    print(f"Listo: {final}  ({duracion(final):.1f} s)")


if __name__ == "__main__":
    main()
