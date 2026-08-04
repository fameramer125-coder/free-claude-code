# Builds the Juthur audio library page from generated outputs.
# Usage: python3 build_library.py <git-sha>
# Audio is streamed via jsDelivr CDN pinned to the given commit.
import os
import sys

sha = sys.argv[1]
base = os.path.dirname(os.path.abspath(__file__))
out_dir = os.path.normpath(os.path.join(base, "..", "output"))
cdn = (
    "https://cdn.jsdelivr.net/gh/fameramer125-coder/free-claude-code@"
    + sha
    + "/audio_factory/output/"
)

rows = []
with open(os.path.join(base, "titles.tsv"), encoding="utf-8") as f:
    for line in f:
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 4:
            rows.append(parts)

cards = []
cats = []
done = 0
for name, cat, title_ar, title_en in rows:
    if cat not in cats:
        cats.append(cat)
    ready = os.path.exists(os.path.join(out_dir, name + ".mp3"))
    if ready:
        done += 1
        player = (
            f'<audio controls preload="none" src="{cdn}{name}.mp3"></audio>'
            f'<a class="dl" href="{cdn}{name}.mp3" download>تحميل ⬇</a>'
        )
    else:
        player = '<span class="soon">قريبًا · Coming soon</span>'
    cards.append(
        f'<div class="card" data-cat="{cat}">'
        f'<h3>{title_ar}</h3><p class="en">{title_en}</p>{player}</div>'
    )

buttons = '<button class="f active" data-cat="all">الكل · All</button>' + "".join(
    f'<button class="f" data-cat="{c}">{c}</button>' for c in cats
)

html = f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>المكتبة الصوتية — جُذور · Juthur Audio Library</title>
<style>
:root {{ --ink:#1a1a1a; --gold:#b8860b; --paper:#faf6ee; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:var(--paper); color:var(--ink);
  font-family:"Amiri","Noto Naskh Arabic",Georgia,serif; padding:2rem 1rem; }}
header {{ text-align:center; max-width:820px; margin:0 auto 2rem; }}
header h1 {{ font-size:2.2rem; }}
header h1 span {{ color:var(--gold); }}
header p {{ margin-top:.6rem; opacity:.8; }}
.count {{ display:inline-block; margin-top:.8rem; border:1px solid var(--gold);
  color:var(--gold); border-radius:2rem; padding:.2rem 1rem; font-size:.95rem; }}
nav {{ display:flex; flex-wrap:wrap; gap:.5rem; justify-content:center;
  max-width:820px; margin:0 auto 1.5rem; }}
nav button {{ font-family:inherit; font-size:1rem; border:1px solid var(--ink);
  background:transparent; border-radius:2rem; padding:.35rem 1.1rem; cursor:pointer; }}
nav button.active {{ background:var(--ink); color:var(--paper); }}
main {{ max-width:820px; margin:0 auto; display:grid; gap:1rem; }}
.card {{ background:#fff; border:1px solid #e5dcc8; border-radius:.8rem;
  padding:1.1rem 1.3rem; box-shadow:0 1px 4px rgba(26,26,26,.06); }}
.card h3 {{ font-size:1.15rem; margin-bottom:.15rem; }}
.card .en {{ font-size:.85rem; opacity:.6; margin-bottom:.7rem; }}
.card audio {{ width:100%; }}
.card .dl {{ display:inline-block; margin-top:.4rem; color:var(--gold);
  font-size:.9rem; text-decoration:none; }}
.card .soon {{ color:#999; font-style:italic; }}
footer {{ text-align:center; margin-top:2.5rem; opacity:.75; font-size:.9rem; }}
footer em {{ color:var(--gold); font-style:normal; }}
</style>
</head>
<body>
<header>
  <h1>المكتبة الصوتية — <span>جُذور</span></h1>
  <p>تسجيلات بصوت د. عادل ف. عامر — شرح المشروع، والأسفار العشرون، والمقاطع القصيرة، والمحتوى الإنجليزي.<br>
  <small>Audio recordings by Dr. Adel F. Amer — cloned-voice production, Juthur Voice Factory.</small></p>
  <span class="count">{done} / 57 تسجيلًا</span>
</header>
<nav>{buttons}</nav>
<main>{"".join(cards)}</main>
<footer>
  <p><em>اللغةُ تُعرَفُ بتقاطعِ الحواسّ.</em></p>
  <p>© 2026 مشروع جُذور · د. عادل ف. عامر · Amer (2024) · جميع الحقوق محفوظة</p>
</footer>
<script>
document.querySelectorAll("nav .f").forEach(function (b) {{
  b.onclick = function () {{
    document.querySelectorAll("nav .f").forEach(function (x) {{ x.classList.remove("active"); }});
    b.classList.add("active");
    var cat = b.dataset.cat;
    document.querySelectorAll(".card").forEach(function (c) {{
      c.style.display = (cat === "all" || c.dataset.cat === cat) ? "" : "none";
    }});
  }};
}});
document.addEventListener("play", function (e) {{
  document.querySelectorAll("audio").forEach(function (a) {{
    if (a !== e.target) a.pause();
  }});
}}, true);
</script>
</body>
</html>"""

out = os.path.join(base, "library.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(f"built {out}: {done} ready of {len(rows)} listed")
