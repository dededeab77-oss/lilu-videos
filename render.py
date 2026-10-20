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


def preparar_imagen(origen, destino):
    """Deja la ilustración en 16:9 (1280x720) SIN estirarla.
    Si ya es casi 16:9 recorta un poquito los bordes; si es muy distinta,
    la centra y rellena los costados con el mismo fondo difuminado."""
    im = Image.open(origen)
    if im.mode != "RGB":
        im = im.convert("RGB")
    ratio = im.width / im.height
    objetivo = W / H
    if abs(ratio - objetivo) / objetivo <= 0.08:
        cubrir = max(W / im.width, H / im.height)
        g = im.resize((round(im.width * cubrir), round(im.height * cubrir)), Image.LANCZOS)
        x, y = (g.width - W) // 2, (g.height - H) // 2
        g.crop((x, y, x + W, y + H)).save(destino)
    else:
        encajar_imagen(origen, destino)


def buscar_imagen(base, numero, indicada):
    """Encuentra la ilustración de una escena.
    1) usa el nombre escrito en story.json si existe;
    2) si no, busca en la carpeta cualquier imagen cuyo nombre contenga
       'escena<numero>' (sin importar mayúsculas, espacios, guiones ni .png.png)."""
    import re
    if indicada and (base / indicada).is_file():
        return base / indicada
    patron = re.compile(rf"escena0*{numero}(?!\d)")
    candidatas = []
    for carpeta in (base, base / "imagenes"):
        if not carpeta.is_dir():
            continue
        for f in sorted(carpeta.iterdir()):
            if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            limpio = re.sub(r"[\s_\-]+", "", f.name.lower())
            if patron.search(limpio):
                candidatas.append(f)
    if candidatas:
        print(f"  imagen encontrada: {candidatas[0].name}")
        return candidatas[0]
    return None


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
def encajar_imagen(origen, destino, ancho=W, alto=H):
    """Acomoda la imagen en ancho x alto sin deformarla; los costados se rellenan con el mismo fondo difuminado."""
    from PIL import ImageFilter
    im = Image.open(origen).convert("RGB")
    cubrir = max(ancho / im.width, alto / im.height)
    fondo = im.resize((int(im.width * cubrir) + 1, int(im.height * cubrir) + 1), Image.LANCZOS)
    fondo = fondo.crop(((fondo.width - ancho) // 2, (fondo.height - alto) // 2,
                        (fondo.width - ancho) // 2 + ancho, (fondo.height - alto) // 2 + alto))
    fondo = fondo.filter(ImageFilter.GaussianBlur(45))
    escala = min(ancho / im.width, alto / im.height)
    nuevo = im.resize((int(im.width * escala), int(im.height * escala)), Image.LANCZOS)
    fondo.paste(nuevo, ((ancho - nuevo.width) // 2, (alto - nuevo.height) // 2))
    fondo.save(destino)


# ----------------------------------------------------------------------
#  DISEÑO "COMPLETA": ilustración a pantalla completa + subtítulos por partes
# ----------------------------------------------------------------------
def paginas_subtitulo(texto, ancho=46, max_lineas=3):
    """Reparte el texto en subtítulos de hasta 3 renglones, cortando por oraciones."""
    import re
    oraciones = re.split(r"(?<=[.!?…])\s+", texto.strip())
    paginas, actual = [], ""
    for o in oraciones:
        prueba = (actual + " " + o).strip()
        if len(textwrap.wrap(prueba, ancho)) <= max_lineas:
            actual = prueba
            continue
        if actual:
            paginas.append(actual)
            actual = ""
        lineas = textwrap.wrap(o, ancho)
        while len(lineas) > max_lineas:
            paginas.append(" ".join(lineas[:max_lineas]))
            lineas = lineas[max_lineas:]
        actual = " ".join(lineas)
    if actual:
        paginas.append(actual)
    return paginas


def clip_completa(imagen, paginas, audio, dur_audio, pausa, salida):
    """paginas = [(png_transparente, inicio, fin_o_None), ...]; la ilustración tiene zoom lento."""
    dur = dur_audio + PAUSA_FINAL + pausa
    frames = int(dur * FPS) + 1
    entradas = ["-loop", "1", "-i", str(imagen)]
    for png, _, _ in paginas:
        entradas += ["-loop", "1", "-framerate", str(FPS), "-i", str(png)]
    entradas += ["-i", str(audio)]
    filtro = (f"[0:v]scale=2560:-2,zoompan=z='min(zoom+0.0005,1.12)':"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}[bg];")
    actual = "[bg]"
    for j, (_, ini, fin) in enumerate(paginas, start=1):
        cond = f"gte(t,{ini:.2f})" if fin is None else f"between(t,{ini:.2f},{fin:.2f})"
        filtro += f"{actual}[{j}:v]overlay=0:0:enable='{cond}'[s{j}];"
        actual = f"[s{j}]"
    filtro += f"{actual}format=yuv420p[v];[{len(paginas) + 1}:a]apad=pad_dur={PAUSA_FINAL + pausa}[a]"
    run(["ffmpeg", "-y"] + entradas + ["-filter_complex", filtro, "-map", "[v]", "-map", "[a]",
         "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
         "-c:a", "aac", "-ar", "44100", "-ac", "2", str(salida)])


# ----------------------------------------------------------------------
#  DISEÑO "MITAD": ilustración cuadrada a la izquierda, texto a la derecha
# ----------------------------------------------------------------------
IMG = 720                       # lado de la ilustración cuadrada
CREMA = (255, 247, 228)
MARRON = (84, 52, 28)
NARANJA = (226, 120, 30)


def preparar_cuadrada(origen, destino, lado=IMG):
    """Ilustración cuadrada. Si ya es casi cuadrada recorta apenas; si no, la centra con fondo difuminado."""
    im = Image.open(origen)
    if im.mode != "RGB":
        im = im.convert("RGB")
    if abs(im.width / im.height - 1) <= 0.08:
        lado_min = min(im.width, im.height)
        x, y = (im.width - lado_min) // 2, (im.height - lado_min) // 2
        im.crop((x, y, x + lado_min, y + lado_min)).resize((lado, lado), Image.LANCZOS).save(destino)
    else:
        encajar_imagen(origen, destino, lado, lado)


def partir_lineas(texto, f, ancho_px):
    """Corta el texto en renglones según el ancho real en píxeles."""
    renglones, actual = [], ""
    for palabra in texto.split():
        prueba = (actual + " " + palabra).strip()
        if f.getlength(prueba) <= ancho_px:
            actual = prueba
        else:
            renglones.append(actual)
            actual = palabra
    if actual:
        renglones.append(actual)
    return renglones


def ajustar_texto(texto, ancho_px, alto_px, minimo=26, maximo=40):
    """Busca el tamaño de letra más grande con el que el texto entra en la caja."""
    for size in range(maximo, minimo - 1, -2):
        f = fuente(size)
        renglones = partir_lineas(texto, f, ancho_px)
        if len(renglones) * int(size * 1.4) <= alto_px:
            return renglones, size
    return None


def paginar(texto, ancho_px, alto_px):
    """Si el texto no entra, lo reparte en 2 o más páginas cortando por oraciones."""
    import re
    if ajustar_texto(texto, ancho_px, alto_px):
        return [texto]
    oraciones = re.split(r"(?<=[.!?…])\s+", texto.strip())
    if len(oraciones) < 2:
        palabras = texto.split()
        m = len(palabras) // 2
        partes = [" ".join(palabras[:m]), " ".join(palabras[m:])]
    else:
        total, acum, corte = len(texto), 0, 1
        for i, o in enumerate(oraciones[:-1], start=1):
            acum += len(o) + 1
            corte = i
            if acum >= total / 2:
                break
        partes = [" ".join(oraciones[:corte]), " ".join(oraciones[corte:])]
    return paginar(partes[0], ancho_px, alto_px) + paginar(partes[1], ancho_px, alto_px)


def panel_png(texto, path, aviso=False, reserva_aviso=False):
    """Panel completo 1280x720: fondo crema + texto a la derecha (la ilustración se pone encima, a la izquierda)."""
    img = Image.new("RGB", (W, H), CREMA)
    d = ImageDraw.Draw(img)
    x0, x1 = IMG + 40, W - 40
    y_top, y_bot = 40, H - (185 if reserva_aviso else 95)
    resultado = ajustar_texto(texto, x1 - x0, y_bot - y_top)
    renglones, size = resultado if resultado else (partir_lineas(texto, fuente(26), x1 - x0), 26)
    f = fuente(size)
    paso = int(size * 1.4)
    y = y_top + max(0, ((y_bot - y_top) - paso * len(renglones)) // 2)
    for r in renglones:
        d.text((x0, y), r, font=f, fill=MARRON)
        y += paso
    d.rectangle([IMG, 0, IMG + 6, H], fill=NARANJA)
    if aviso:
        d.rounded_rectangle([x0, H - 165, x1, H - 105], radius=28, fill=NARANJA)
        d.text(((x0 + x1) // 2, H - 135), "¡Ahora te toca a vos!", font=fuente(32),
               fill=(255, 255, 255), anchor="mm")
    d.text(((IMG + W) // 2, H - 45), "Lilu la Exploradora", font=fuente(26), fill=NARANJA, anchor="mm")
    img.save(path)


def clip_mitad(img_cuadrada, paginas, audio, dur_audio, pausa, salida):
    """paginas = [(png, segundo_en_que_aparece), ...]. La ilustración tiene zoom lento."""
    dur = dur_audio + PAUSA_FINAL + pausa
    frames = int(dur * FPS) + 1
    entradas = ["-loop", "1", "-i", str(img_cuadrada)]
    for png, _ in paginas:
        entradas += ["-loop", "1", "-framerate", str(FPS), "-i", str(png)]
    entradas += ["-i", str(audio)]
    idx_audio = len(paginas) + 1
    filtro = (f"[0:v]scale={IMG * 2}:{IMG * 2},zoompan=z='min(zoom+0.0005,1.12)':"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={IMG}x{IMG}:fps={FPS}[img];")
    actual = "[1:v]"
    for j in range(1, len(paginas)):
        etiqueta = f"[o{j}]"
        filtro += f"{actual}[{j + 1}:v]overlay=0:0:enable='gte(t,{paginas[j][1]:.2f})'{etiqueta};"
        actual = etiqueta
    filtro += f"{actual}[img]overlay=0:0,format=yuv420p[v];[{idx_audio}:a]apad=pad_dur={PAUSA_FINAL + pausa}[a]"
    run(["ffmpeg", "-y"] + entradas + ["-filter_complex", filtro, "-map", "[v]", "-map", "[a]",
         "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
         "-c:a", "aac", "-ar", "44100", "-ac", "2", str(salida)])


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

    diseno = story.get("diseno", "completa")  # "completa" (imagen a pantalla completa) o "mitad" (imagen + texto al costado)
    print(f"Diseño: {diseno}")
    for i, esc in enumerate(story["escenas"], start=1):
        print(f"Escena {i}: {esc['texto'][:50]}...")
        img = buscar_imagen(base, i, esc.get("imagen"))
        if img is None:
            print("  (sin imagen: uso lámina de prueba)")
            img = tmp / f"prueba_{i}.png"
            lamina_prueba(img, i, story["titulo"])
        else:
            print(f"  imagen: {Path(img).name}")
        audio = tmp / f"voz_{i}.mp3"
        generar_voz(esc["texto"], voz, vel, tono, audio)
        dur_audio = duracion(audio)
        pausa = float(esc.get("pausa_segundos", 0) or 0)
        clip = tmp / f"clip_{i}.mp4"

        palabras = len(esc["texto"].split())
        if dur_audio > 45:
            print(f"  AVISO: esta escena dura {dur_audio:.0f} s ({palabras} palabras); "
                  f"conviene dividirla en dos escenas con dos imágenes.")

        if diseno == "completa":
            prep = tmp / f"img_{i}.png"
            preparar_imagen(img, prep)
            partes = paginas_subtitulo(esc["texto"])
            total = sum(len(p) for p in partes)
            paginas, inicio = [], 0.0
            for k, p in enumerate(partes):
                png = tmp / f"sub_{i}_{k}.png"
                subtitulo_png(p, png)
                dur_p = dur_audio * len(p) / total
                es_ultima = k == len(partes) - 1
                fin = None if (es_ultima and pausa <= 0) else (dur_audio if es_ultima else inicio + dur_p)
                paginas.append((png, inicio, fin))
                inicio += dur_p
            if pausa > 0:  # durante la pausa se muestra "¡Ahora te toca a vos!"
                png = tmp / f"sub_{i}_aviso.png"
                subtitulo_png("¡Ahora te toca a vos!", png)
                paginas.append((png, dur_audio, None))
            if len(partes) > 1:
                print(f"  subtítulos en {len(partes)} partes")
            clip_completa(prep, paginas, audio, dur_audio, pausa, clip)
        else:
            prep = tmp / f"cuad_{i}.png"
            preparar_cuadrada(img, prep)
            ancho_txt = (W - 40) - (IMG + 40)
            alto_txt = H - (185 if pausa > 0 else 95) - 40
            partes = paginar(esc["texto"], ancho_txt, alto_txt)
            total = sum(len(p) for p in partes)
            paginas, inicio = [], 0.0
            for k, p in enumerate(partes):
                png = tmp / f"panel_{i}_{k}.png"
                panel_png(p, png, reserva_aviso=pausa > 0)
                paginas.append((png, inicio))
                inicio += dur_audio * len(p) / total
            if pausa > 0:  # durante la pausa se muestra el cartel "¡Ahora te toca a vos!"
                png = tmp / f"panel_{i}_aviso.png"
                panel_png(partes[-1], png, aviso=True, reserva_aviso=True)
                paginas.append((png, dur_audio))
            if len(partes) > 1:
                print(f"  texto largo: se muestra en {len(partes)} partes")
            clip_mitad(prep, paginas, audio, dur_audio, pausa, clip)
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
