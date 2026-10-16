# Planýrka

Poetický fejeton na kartonu: Play z mikroteček → deset zastávek po Planýrce (kresba, mluvené slovo
a titulky, které se po slovech rozsvěcují) → závěrečné titulky s tlačítkem „Přehrát znovu“.
Dole běží stavový řádek – tečka za každou scénu, které se postupně rozsvěcují – a vpravo tlačítko
Pauza / Pokračovat (funguje i mezerník; zastaví zvuk i všechny animace).

## Spuštění

Stačí otevřít `index.html` v prohlížeči. Případně přes lokální server
(posílá `no-cache`, takže se po úpravách nikdy nenačte stará verze z mezipaměti):

```bash
python3 tools/server.py
```

a pak <http://localhost:8137>.

**Náhled všech scén** (i těch, které ještě nemají nahrávku – ukážou se s názvem na 5 s):
<http://localhost:8137/?nahled>. Začít od konkrétní scény: `?od=5` (lze spojit: `?nahled&od=5`).

## Struktura

```
index.html            kostra stránky
scénář.md             scénář celého příběhu
css/style.css         vzhled (barvy, záře slov, rozměry)
js/play-button.js     tlačítko Play z mikroteček (canvas + GSAP)
js/app.js             průběh scén, zvuk, titulky
data/scenes.js        obsah: 10 scén (obrázek, zvuk, titulky s časy slov) + závěrečné titulky
assets/               obrázky (WebP), zvuk pro web (M4A) a text toho, co v nahrávce zaznělo (.txt)
assets/puvodni/       originály (PNG s průhledností, surové nahrávky)
assets/strih/         prostříhané nahrávky (FLAC) + seznam zásahů (.json)
tools/obrazky.py      příprava obrázku pro web
tools/server.py       lokální server pro vývoj
.github/workflows/    nasazení na GitHub Pages
tools/strih.py        prostříhání nahrávky (přeřeky, „ehm“, nádechy, ticho)
tools/zvuk.py         studiová úprava nahrávky
tools/prepis.py       výroba titulků z nahrávky
porovnani-zvuku.html  srovnání nahrávek před úpravou a po ní (všechny scény)
kontrola-strihu.html  kontrola střihu – poslech každého zásahu před a po
```

## Obrázky

Originál (PNG s průhledným pozadím) patří do `assets/puvodni/`, pro web se připraví:

```bash
uv run --no-project --with pillow --with numpy python tools/obrazky.py assets/puvodni/03-boby.png assets/03-boby.webp
```

Ořízne průhledné okraje, uhlové kresby převede na čistě šedou a uloží WebP „vypečený“ na bílou.
Web obrázky s kartonem násobí (bílá = karton), takže to vypadá stejně jako s průhledností,
ale soubory jsou zhruba 10× menší než PNG.

## Nahrávka a titulky ke scéně

0. Surovou nahrávku dej do `assets/puvodni/` (např. `02-parkoviste.m4a`), text scény ze scénáře
   do `assets/02-parkoviste.txt` a nahrávku prostříhej. Když se při nahrávání spleteš,
   udělej pauzu a přečti celou větu znovu – vystřihne se starší pokus:

   ```bash
   uv run --no-project --python 3.11 --with faster-whisper --with torch==2.5.1 --with torchaudio==2.5.1 --with "numpy<2" --with scipy --with soundfile --with certifi python tools/strih.py assets/puvodni/02-parkoviste.m4a assets/02-parkoviste.txt assets/strih/02-parkoviste.flac
   ```

   Výsledek zkontroluj na <http://localhost:8137/kontrola-strihu.html>.

1. Prostříhanou nahrávku uprav na „studiový“ zvuk (EQ, de-esser, komprese, hlasitost −16 LUFS; časování se nemění):

   ```bash
   uv run --no-project --with numpy --with scipy --with soundfile --with pyloudnorm python tools/zvuk.py assets/strih/02-parkoviste.flac assets/02-parkoviste.m4a
   ```

2. Text v `assets/02-parkoviste.txt` uprav tak, aby přesně odpovídal tomu, co v nahrávce zaznělo
   (odchylky ukáže kontrola střihu). Případně si nech udělat přepis a oprav ho:

   ```bash
   uv run --no-project --with faster-whisper python tools/prepis.py text assets/puvodni/02-parkoviste.m4a > assets/02-parkoviste.txt
   ```

3. Časy slov podle textu:

   ```bash
   uv run --no-project --with torch==2.5.1 --with torchaudio==2.5.1 --with "numpy<2" --with soundfile --with certifi python tools/prepis.py casy assets/02-parkoviste.m4a assets/02-parkoviste.txt
   ```

4. Ve scéně v `data/scenes.js` nastav `audio:` a vlož vypsané `cues: [...]`.
   Překlep v titulku jde opravit přímo v `cues` – časy zůstanou.

Závěrečné titulky jsou poslední položka v `data/scenes.js` (`credits`).

Doladění: v `js/app.js` nahoře je `WORD_OFFSET` (posun všech slov),
`CUE_LEAD` / `CUE_HOLD` (kdy se věta objeví a jak dlouho zůstane)
a `AUDIO_DELAY` (prodleva mezi obrázkem a zvukem).

## Nasazení na GitHub Pages

Workflow `.github/workflows/pages.yml` při každém pushi do větve `main` sestaví web a nasadí ho.
Na web jde jen to, co stránka potřebuje (`index.html`, `css/`, `js/`, `data/`, obrázky WebP a nahrávky
M4A – zhruba 7 MB); originály, prostříhané FLAC, nástroje a kontrolní stránky ne. Před nasazením
se zkontroluje, že nechybí žádný soubor, na který stránka odkazuje.

1. Na GitHubu založ repozitář (pro GitHub Pages zdarma musí být veřejný) a nahraj do něj projekt:

   ```bash
   git init -b main && git add . && git commit -m "Planýrka"
   git remote add origin git@github.com:UZIVATEL/REPOZITAR.git && git push -u origin main
   ```

2. V repozitáři **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. V záložce **Actions** doběhne „Nasadit na GitHub Pages“ a web bude na
   `https://UZIVATEL.github.io/REPOZITAR/`. Další nasazení proběhne samo po každém pushi
   (nebo ručně: Actions → Nasadit na GitHub Pages → Run workflow).

Ve veřejném repozitáři je vidět všechno, co je v něm uložené – i surové nahrávky v `assets/puvodni/`
a `assets/strih/`. Pokud je tam mít nechceš, odkomentuj je v `.gitignore`.

