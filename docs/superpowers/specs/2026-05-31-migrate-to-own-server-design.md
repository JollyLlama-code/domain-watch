# Költöztetés: LPNTY → saját szerver ("szerver")

**Dátum:** 2026-05-31
**Cél:** A teljes domain-watch + microware backorder rendszert átvinni a haver
LPNTY (Windows Server 2022) gépéről a felhasználó saját gépére (hostname:
`szerver`), és az LPNTY-t teljesen leszerelni.

## Kiindulási helyzet

- Az LPNTY-n fut ma: `DomainWatch` (check.py, 1 perc), `BackorderAPI`
  (uvicorn :8000), `CloudflaredTunnel` (named tunnel → `backorder.lappantyu.com`).
- Microware API csak whitelistelt IP-ről hívható (max 6 IP). Ma whitelistelt:
  `178.48.104.118` (LPNTY publikus IP).
- A backorder ma `enabled:false`, `dry_run:true` — éles pénz még nem folyt.
- `secrets.env` és `gittoken.txt` gitignore-oltak, nincsenek a repóban.

## Eldöntött elágazások (felhasználói döntések)

1. **Cél gép:** saját gép, hostname `szerver`. Otthoni, **dinamikus publikus IP**.
2. **Fix-IP a microware felé:** **dinamikus re-whitelist** út — a szerver
   figyeli a saját publikus IP-jét, IP-váltáskor riaszt, a felhasználó kézzel
   újra-whitelistel a portálon. (VPS relay és LPNTY-relay elvetve.)
3. **LPNTY sorsa:** **teljes leválás** — a 3 task ott leállítva/törölve.
4. **seen.json:** **tiszta start** — üres seen.json, az első futás csendben
   beolvassa az aktuális listát (seed-skip), nincs értesítés-cunami.
5. **IP-őr ütemezés:** **beépítve a check.py-ba** — minden percben fut, külön
   task nélkül.

## Architektúra a költözés után

```
telefon-tap → Cloudflare → cloudflared (szerver) → localhost:8000 (FastAPI)
                                                         │
check.py (1 perc, szerver) ── scrape domain.hu ── ntfy push
   └── IP-őr: publikus IP figyelés, váltáskor ntfy riasztás
backorder submit ── szerver kimenő IP ──> api.microware.hu (whitelist!)
```

Csak a microware felé menő **kimenő** hívásnak kell whitelistelt IP. A scrape,
az ntfy, és a bejövő tunnel-tap dinamikus IP-n is működik.

## Komponensek

### 1. Új: dinamikus IP-őr (`ip_guard.py`)

Önálló modul, egyetlen feladat: tudjuk-e, hogy a szerver mostani publikus IP-je
meg van-e whitelistelve a microware-nél.

- `current_public_ip() -> str | None` — GET `https://api.ipify.org`, rövid
  timeout, hiba esetén `None` (nem dobunk a percenkénti futásban).
- Állapot: `whitelisted_ip.json` a repo gyökerében (gitignore-olt), tartalma
  `{"ip": "x.x.x.x", "noticed": "<iso>"}` — az utoljára *látott* és a
  felhasználónak már bejelentett IP.
- `check_and_alert(cfg)` a `check.py` minden futásában:
  - lekéri a mostani IP-t; ha `None`, kilép csendben,
  - ha eltér a tárolt `ip`-től → ntfy push:
    *„⚠️ Szerver IP változott: <régi> → <új>. Whitelisteld a microware
    portálon (admin.microware.hu → API hozzáférés beállítása), különben az
    éles backorder 10401-gyel elhal."* — majd frissíti `whitelisted_ip.json`-t
    az új IP-re,
  - ha egyezik → nem csinál semmit.
- Első futáskor (nincs `whitelisted_ip.json`) eltárolja az aktuális IP-t és
  egy **tájékoztató** push-t küld („kiinduló IP: <ip> — ezt whitelisteld").

A push az `ntfy_send` / `build_ntfy_headers` meglévő segédeit használja
(`check.py`), nem vezet be új értesítési csatornát.

### 2. Módosítás: 10401 felszínre hozása (`backorder_api.py`)

A backorder végrehajtáskor (`register_backorder` hívás után), ha az eredmény
`error_number == 10401` ("Authentication failed" — ami nálunk a leggyakrabban
nem whitelistelt IP-t jelent), külön ntfy riasztás menjen:
*„Backorder ELHALT (<domain>): 10401 — valószínűleg nincs whitelistelve a
szerver IP-je. Aktuális IP: <ip>."* Így a pénzes pillanatban azonnal látszik az
ok, nem csak egy néma hiba.

> Megjegyzés: a 10401 jelenthet rossz `username`-et is (a Google-OAuth-os
> fiók HTTP Basic usernevének kérdése még nyitott). A riasztás szövege ezért
> „valószínűleg" — mindkét okot felsorolja röviden.

### 3. Általánosított task-regisztráló (`register_server_tasks.ps1`)

Az `register_lpnty_tasks.ps1` újrahasznosítása, paraméterezve a szerverre:

- Tetején `param(...)` blokk: `-PyExe`, `-CfExe`, `-CfConfig`, `-AdminSid`,
  `-DwDir` (alapértékek a szerverhez igazítva, de felülírhatók).
- Ugyanaz az idempotens minta (unregister → register), S4U logon, AtStartup
  trigger, `MultipleInstances IgnoreNew`.
- Mindhárom taskot regisztrálja: **`DomainWatch`** (python check.py, 1 perces
  trigger — ez az LPNTY-n külön volt, most ide is bekerül), `BackorderAPI`
  (uvicorn :8000), `CloudflaredTunnel`.
- A szerver-specifikus értékeket (python.exe pontos útvonala, admin SID, a
  cloudflared elérési útja) a futtatás előtt a szerveren kell kideríteni; a
  bootstrap checklist (lent) megmondja hogyan.

### 4. cloudflared tunnel a szerveren

Új named tunnel a szerveren, és a `backorder.lappantyu.com` CNAME átírása az új
tunnelre a Cloudflare-ben (a `make_cf_config.py` írja a `config.yml`-t). A régi
LPNTY tunnel route-ot törölni kell, hogy ne versengjen két tunnel ugyanazért a
hostnévért.

## Végrehajtási modell

A jelenlegi Claude-munkamenet a `DESKTOP-FTEVD5O` gépen fut, **nem a
szerveren**. Ezért a leszállítandó: **committolt scriptek + egy `BOOTSTRAP.md`
checklist**, amit a felhasználó RDP-n a szerveren futtat — pontosan úgy, ahogy
az LPNTY-nél is történt. (Egyezik a felhasználó preferenciájával: committolt
scriptek a beillesztett blokkok helyett.)

## Bootstrap checklist (a szerveren futtatva)

1. `git clone` / `git pull` a domain-watch repo a szerverre.
2. `pip install -r requirements.txt`.
3. `secrets.env` létrehozása (kézzel, NEM a repóból): `MICROWARE_API_PASSWORD`
   + új `BACKORDER_HMAC_SECRET`. **A `gittoken.txt`-t és a régi `secrets.env`-t
   NEM másoljuk át az LPNTY-ről** — frissen jönnek létre.
4. Python + cloudflared elérési útjának és az admin SID kiderítése
   (`(Get-Command python).Source`, `whoami /user`); ezekkel hívni a
   `register_server_tasks.ps1`-t.
5. cloudflared: named tunnel + `config.yml` (`make_cf_config.py`), CNAME
   `backorder.lappantyu.com` átírása az új tunnelre.
6. `register_server_tasks.ps1` futtatása → 3 task él, smoke a tunnelen
   (`smoke_lpnty.py <tunnel_url>`).
7. `config.json` `backorder.tunnel_url` beállítása az élő tunnel URL-re.
8. Microware portál: a szerver aktuális publikus IP felvétele a whitelistre,
   `178.48.104.118` törlése.
9. Dry-run smoke a valós microware ellen (request body ellenőrzés, nincs
   költés) — `dry_run:true`, `enabled:true`.
10. **LPNTY leszerelés:** a 3 task (`DomainWatch`, `BackorderAPI`,
    `CloudflaredTunnel`) letiltása/törlése, régi tunnel route eltávolítása.

## Éles élesítés (külön, óvatos lépés — nem része ennek a költözésnek)

A `dry_run:false` átállítás és az első valós, szándékosan alacsony értékű
célpontra leadott backorder továbbra is külön, kézi döntés marad a sikeres
költözés és dry-run smoke után. Ez a spec a *költöztetést* fedi, nem az első
éles elkapást.

## Tesztelés

- `ip_guard.py`: egységtesztek mockolt `current_public_ip`-pel — első futás
  (nincs állapotfájl), egyező IP (nincs push), eltérő IP (push + állapotírás),
  `None` IP (csendes kilépés). A meglévő `tests/` mintát követi.
- 10401-riasztás: a `register_backorder` eredményének mockolásával ellenőrizni,
  hogy `error_number == 10401`-nél megy a push.
- `register_server_tasks.ps1`: csak kézi futtatás a szerveren (Task Scheduler
  nem unit-tesztelhető); a `Get-ScheduledTaskInfo` állapotkiírás a verifikáció.

## Nyitott / kockázat

- **Dinamikus IP késleltetés:** ha az IP épp egy backorder-tap előtt vált, és
  még nem whiteliszteltél újra, az éles hívás elhal (10362/10401). A 10401-
  riasztás és a percenkénti IP-őr ezt minimalizálja, de nem zárja ki — ez a
  választott út tudatos kompromisszuma.
- **Microware username:** a 10401 oka lehet a még nem visszaigazolt HTTP Basic
  username is (Google-OAuth fiók). A riasztásszöveg ezért mindkettőt említi.
- A `seen.json` tiszta start miatt az első ~31 nap előzménye elveszik; ez
  tudatos döntés (egyszerűség az előzmény helyett).
