"""Кадры отчёта + озвучка по фразам + субтитры + плавные переходы → bizpipe_demo.mp4"""
import json, re, subprocess, sys
from pathlib import Path
V = Path(sys.argv[1]); OUT = sys.argv[2]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
report = (V / "report.html").read_text(encoding="utf-8")
Q = "document.querySelector"
row = lambda sel: f"document.querySelector('{sel}').closest('.row')"
SCENES = {  # (JS: к чему прокрутить, что раскрыть, отступ, JS: что подсветить, ширина окна)
 "report:top":          (f"{Q}('main')", None, 0, f"{Q}('.stats')", 1280),
 "report:rejected":     (f"{Q}('h2:nth-of-type(3)')", None, 20, f"{Q}('h2:nth-of-type(3) + table')", 1280),
 "report:card_top":     (f"{Q}('#keiki')", None, 16, f"{Q}('#keiki .head')", 900),
 "report:card_risks":   (row("#keiki .risk"), None, 24, row("#keiki .risk"), 900),
 "report:card_sources": (f"{Q}('#keiki details')", "#keiki details", 16, f"{Q}('#keiki details summary')", 900),
 "report:textai_head":  (f"{Q}('#text-ai')", None, 16, f"{Q}('#text-ai .head')", 900),
 "report:textai_steps": (row("#text-ai .steps"), None, 150, row("#text-ai .steps"), 900),
 "report:rating":       (f"{Q}('h2:nth-of-type(1)')", None, 20, f"{Q}('h2:nth-of-type(1) + .scroll')", 1280),
}
def inject(sel, open_sel, off, hl, width):
    js = f"""<style>.hl{{outline:3px solid #3a5ea8;outline-offset:4px;border-radius:6px}}</style>
<script>addEventListener('load',()=>{{
 {f"document.querySelector('{open_sel}').open=true;" if open_sel else ""}
 const h={hl}; if(h) h.classList.add('hl');
 const e={sel}; const m=document.querySelector('main');
 const y=e.getBoundingClientRect().top-{off}; m.style.transform='translateY('+(-y)+'px)';
}});</script>"""
    return report.replace("</body>", js + "</body>")
SLIDE = """<!DOCTYPE html><html lang=ru><meta charset=utf-8><style>
body{{margin:0;width:1280px;height:720px;display:flex;flex-direction:column;justify-content:center;padding:0 110px 90px;box-sizing:border-box;
font:30px/1.45 -apple-system,'Segoe UI',sans-serif;background:#faf9f6;color:#1c1c1c}}
h1{{font-size:50px;margin:0 0 24px}} .s{{color:#6b6b6b}} ul{{margin:0;padding-left:30px}} li{{margin:8px 0}}
code{{background:#fff;border:1px solid #e4e2dc;padding:6px 14px;border-radius:8px;font-size:28px}}</style><body>{}</body></html>"""
SLIDES = {
 "slide:title": "<h1>Конвейер проверки бизнес-моделей</h1><div class=s>15 моделей → критерии отбора → агент-скептик + независимая проверка источников → карточки и рейтинг</div><p>Критерий: шанс за 3 месяца запустить тест и получить первую оплату</p>",
 "slide:howto": "<h1>Как пользоваться</h1><p><code>python run.py all</code> — весь конвейер одной командой</p><p><code>config.yaml</code> — критерии, веса факторов и пороги вердиктов</p><p class=s>Отчёт: bazzs.github.io/bizpipe</p>",
 "slide:next": "<h1>Что дальше</h1><b>В полной версии</b><ul><li>запуск по расписанию из нескольких источников</li><li>агенты на разных ИИ-провайдерах</li><li>экран, где человек закрывает спорные пункты</li></ul><p><b>Убрал бы из задания:</b> жёсткий фильтр «компания на $300 млн» → «есть 3+ компании, которые уже берут за это деньги»</p>",
 "slide:issues": "<h1>Что работает криво</h1><ul><li>фильтр слишком строг к «лицензии» — правила исправлены, на API не перепроверены</li><li>оценщик щедр на высокие баллы</li><li>рейтинг между прогонами плавает на ~10 баллов</li><li>факты агентов на английском, в отчёте — машинный перевод</li></ul><p class=s>Полный список — KNOWN_ISSUES.md в репозитории</p>",
}
SAY = {"Y Combinator": "Уай Комбинатор", "Vassar Robotics": "Вассар Роботикс", "Edexia": "Эдексия", "Keiki": "Кейки",
       "Contrario": "Контрарио", "Tinfoil": "Тинфойл", "Text.ai": "Текст эй-ай", "xAI": "икс эй-ай", "AI-": "эй-ай ",
       "пять–шестнадцать": "пять — шестнадцать"}
CAP = """<!DOCTYPE html><html><meta charset=utf-8><style>body{{margin:0;width:1280px;height:720px;position:relative;overflow:hidden}}
img{{position:absolute;inset:0;width:1280px;height:720px}} .c{{position:absolute;left:0;right:0;bottom:0;padding:16px 70px 20px;
background:rgba(20,22,28,.82);color:#fff;font:26px/1.35 -apple-system,'Segoe UI',sans-serif;text-align:center}}</style>
<body><img src="{img}"><div class=c>{text}</div></body></html>"""
def chrome(html: Path, png: Path, width: int):
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--force-device-scale-factor={1920 / width}",
                    f"--window-size={width},{round(width * 9 / 16)}", "--virtual-time-budget=3000", f"--screenshot={png}",
                    html.as_uri()], check=True, capture_output=True)
def dur(f):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                                capture_output=True, text=True).stdout)
segs = []
for i, s in enumerate(json.load(open(V / "scenes.json")), 1):
    shot, base = s["shot"], V / f"s{i:02d}"
    html = base.with_suffix(".html")
    html.write_text(SLIDE.format(SLIDES[shot]) if shot.startswith("slide:") else inject(*SCENES[shot]), encoding="utf-8")
    chrome(html, base.with_suffix(".png"), 1280 if shot.startswith("slide:") else SCENES[shot][4])
    n = len(s["lines"])
    for j, line in enumerate(s["lines"]):
        p = V / f"s{i:02d}_{j}"
        spoken = line
        for a, b in SAY.items():
            spoken = spoken.replace(a, b)
        subprocess.run(["say", "-v", "Milena", "-r", "185", "-o", str(p) + ".aiff", spoken], check=True)
        (p.with_suffix(".html")).write_text(CAP.format(img=base.with_suffix(".png").as_uri(), text=line), encoding="utf-8")
        chrome(p.with_suffix(".html"), p.with_suffix(".png"), 1280)
        lead = 0.4 if j == 0 else 0.1          # пауза перед фразой: в начале сцены длиннее
        tail = 0.6 if j == n - 1 else 0.25
        d = lead + dur(str(p) + ".aiff") + tail
        vf = ["scale=1920:1080", "format=yuv420p"]
        if j == 0: vf.append("fade=t=in:st=0:d=0.35")
        if j == n - 1: vf.append(f"fade=t=out:st={d - 0.35:.2f}:d=0.35")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "25", "-i", str(p) + ".png", "-i", str(p) + ".aiff",
                        "-filter_complex", f"[0:v]{','.join(vf)}[v];[1:a]adelay={int(lead * 1000)},apad,aresample=44100[a]",
                        "-map", "[v]", "-map", "[a]", "-t", f"{d:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-c:a", "aac", "-b:a", "128k", "-ac", "1", str(p) + ".mp4"], check=True)
        segs.append(str(p) + ".mp4")
    print(i, shot, "ok")
(V / "list.txt").write_text("".join(f"file '{x}'\n" for x in segs))
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(V / "list.txt"), "-c", "copy",
                "-movflags", "+faststart", OUT], check=True)
print("done", round(dur(OUT), 1), "s")
