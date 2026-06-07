#!/usr/bin/env python3
"""Generate a standalone HTML page to thumbs-up / thumbs-down rate every recipe.

  python3 make_rating_page.py --corpus recipes_tagged.json --out recipe_ratings.html

Open the generated file in a browser (off disk is fine), then for each recipe click
👍 or 👎 (or leave it unrated). Ratings persist in the browser (localStorage) so you can
rate across several sittings. When done, click "Download ratings JSON" and feed that file
to apply_ratings.py to store the ratings in the corpus.

The page pre-fills each recipe's current rating (from the corpus), so it doubles as an
editor for later rating passes as you cook more meals.
"""
import argparse
import html
import json


def key_of(rec):
    """Stable per-recipe key (matches apply_ratings.py): recipe_url, else title."""
    url = (rec.get("recipe_url") or "").strip()
    return url or "title::" + (rec.get("title") or "").strip()


def _img(rec):
    return rec.get("recipe_image") or rec.get("pinterest_image") or ""


def card_html(rec):
    k = html.escape(key_of(rec), quote=True)
    title = html.escape(rec.get("title") or "(untitled)")
    url = html.escape(rec.get("recipe_url") or "", quote=True)
    img = html.escape(_img(rec), quote=True)
    init = rec.get("rating") if rec.get("rating") in ("up", "down") else "none"
    proto = html.escape((rec.get("protein") or "") + (" · pasta" if rec.get("is_pasta") else ""))
    dish = html.escape(rec.get("dish_type") or "")
    photo = (f'<img class="photo" loading="lazy" src="{img}" alt="" '
             f'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
             if img else "")
    link = f'<a href="{url}" target="_blank" rel="noopener">{title}</a>' if url else title
    return f"""<div class="card" data-key="{k}" data-init="{init}" data-title="{html.escape((rec.get('title') or '').lower(), quote=True)}">
  {photo}<div class="noimg" style="display:{'none' if img else 'flex'}">no photo</div>
  <div class="meta"><div class="title">{link}</div><div class="tag">{proto}{' · ' + dish if dish else ''}</div></div>
  <div class="rate">
    <button class="up" data-v="up" title="Thumbs up">👍</button>
    <button class="clear" data-v="none" title="Clear rating">⌀</button>
    <button class="down" data-v="down" title="Thumbs down">👎</button>
  </div>
</div>"""


PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Rate recipes</title>
<style>
 :root{{--ink:#23201c;--muted:#7a736a;--line:#e7e1d8;--accent:#9c5b34;--bg:#faf7f2;
   --up:#2f8f4e;--down:#b4452f}}
 *{{box-sizing:border-box}}
 body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);
   background:var(--bg);margin:0;line-height:1.45}}
 .bar{{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line);
   padding:12px 18px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;
   box-shadow:0 1px 4px rgba(0,0,0,.05)}}
 .bar h1{{font-size:18px;margin:0 8px 0 0}}
 .counts{{font-size:14px;color:var(--muted)}}
 .counts b{{color:var(--ink)}}
 .bar input[type=search]{{padding:7px 10px;border:1px solid var(--line);border-radius:8px;font-size:14px;min-width:180px}}
 .bar button,.filterbtn{{padding:7px 12px;border:1px solid var(--line);border-radius:8px;background:#fff;
   cursor:pointer;font-size:13px}}
 .filterbtn.active{{background:var(--accent);color:#fff;border-color:var(--accent)}}
 .spacer{{flex:1}}
 .grid{{max-width:1100px;margin:18px auto;padding:0 16px 60px;display:grid;
   grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}}
 .card{{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;
   flex-direction:column}}
 .photo,.noimg{{width:100%;height:170px;object-fit:cover;background:#efe9df}}
 .noimg{{align-items:center;justify-content:center;color:#b8ad9c;font-size:13px;letter-spacing:1px}}
 .meta{{padding:10px 12px 4px;flex:1}}
 .title{{font-size:15px;font-weight:600;margin-bottom:2px}}
 .title a{{color:var(--ink);text-decoration:none;border-bottom:1px solid rgba(156,91,52,.3)}}
 .tag{{color:var(--muted);font-size:12.5px}}
 .rate{{display:flex;gap:8px;padding:10px 12px}}
 .rate button{{flex:1;padding:8px;border:1px solid var(--line);border-radius:8px;background:#fff;
   cursor:pointer;font-size:18px;line-height:1;opacity:.45}}
 .card[data-cur=up] .up{{opacity:1;background:#eaf5ee;border-color:var(--up)}}
 .card[data-cur=down] .down{{opacity:1;background:#f7ece9;border-color:var(--down)}}
 .card[data-cur=none] .clear{{opacity:1;border-color:var(--muted)}}
</style></head><body>
<div class="bar">
  <h1>Rate recipes</h1>
  <span class="counts" id="counts"></span>
  <span class="spacer"></span>
  <input type="search" id="search" placeholder="filter by title…">
  <span><button class="filterbtn active" data-f="all">All</button><button class="filterbtn" data-f="none">Unrated</button><button class="filterbtn" data-f="up">👍</button><button class="filterbtn" data-f="down">👎</button></span>
  <button id="dl">⬇ Download ratings JSON</button>
  <label class="filterbtn" style="cursor:pointer">⬆ Load JSON<input type="file" id="load" accept="application/json" hidden></label>
  <button id="reset" title="Clear browser ratings and revert to the corpus values">Reset</button>
</div>
<div class="grid" id="grid">
{cards}
</div>
<script>
const LS="csaRatings:v1";
const cards=[...document.querySelectorAll('.card')];
let state=JSON.parse(localStorage.getItem(LS)||'{{}}');
function curOf(c){{const k=c.dataset.key; return (k in state)?state[k]:c.dataset.init;}}
function paint(){{
  let up=0,down=0,none=0;
  for(const c of cards){{const v=curOf(c); c.dataset.cur=v; if(v==='up')up++;else if(v==='down')down++;else none++;}}
  document.getElementById('counts').innerHTML=
    `<b>${{up}}</b> 👍 · <b>${{down}}</b> 👎 · <b>${{none}}</b> unrated · ${{cards.length}} total`;
}}
function set(c,v){{const k=c.dataset.key; if(v===c.dataset.init && v==='none') delete state[k]; else state[k]=v;
  localStorage.setItem(LS,JSON.stringify(state)); paint();}}
cards.forEach(c=>c.querySelector('.rate').addEventListener('click',e=>{{
  const b=e.target.closest('button'); if(b) set(c,b.dataset.v);}}));
// filters + search
let filter='all';
function applyView(){{const q=document.getElementById('search').value.trim().toLowerCase();
  for(const c of cards){{const v=curOf(c);
    const okF=filter==='all'||v===filter; const okQ=!q||c.dataset.title.includes(q);
    c.style.display=(okF&&okQ)?'':'none';}}}}
document.querySelectorAll('.filterbtn[data-f]').forEach(b=>b.addEventListener('click',()=>{{
  document.querySelectorAll('.filterbtn[data-f]').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); filter=b.dataset.f; applyView();}}));
document.getElementById('search').addEventListener('input',applyView);
// export: every card, explicit up/down/none so apply_ratings can also clear
document.getElementById('dl').addEventListener('click',()=>{{
  const out={{}}; for(const c of cards) out[c.dataset.key]=curOf(c);
  const blob=new Blob([JSON.stringify(out,null,1)],{{type:'application/json'}});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download='ratings.json'; a.click();}});
document.getElementById('load').addEventListener('change',ev=>{{
  const f=ev.target.files[0]; if(!f)return; const r=new FileReader();
  r.onload=()=>{{try{{const d=JSON.parse(r.result); for(const k in d) state[k]=d[k];
    localStorage.setItem(LS,JSON.stringify(state)); paint(); applyView();
    alert('Loaded '+Object.keys(d).length+' ratings.');}}catch(e){{alert('Bad JSON: '+e);}}}}; r.readAsText(f);}});
document.getElementById('reset').addEventListener('click',()=>{{
  if(confirm('Clear ratings saved in this browser and revert to the corpus values?')){{
    state={{}}; localStorage.removeItem(LS); paint(); applyView();}}}});
paint(); applyView();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="recipes_tagged.json")
    ap.add_argument("--out", default="recipe_ratings.html")
    args = ap.parse_args()

    corpus = json.load(open(args.corpus))
    cards = "\n".join(card_html(r) for r in corpus)
    with open(args.out, "w") as f:
        f.write(PAGE.format(cards=cards))
    rated = sum(1 for r in corpus if r.get("rating") in ("up", "down"))
    print(f"wrote {args.out} — {len(corpus)} recipes ({rated} already rated)")


if __name__ == "__main__":
    main()
