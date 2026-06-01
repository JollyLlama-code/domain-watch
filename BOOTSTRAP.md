# Szerver bootstrap — domain-watch + backorder

Ezt a checklist-et a **saját szerveren** (hostname `szerver`) futtasd, RDP-ről.
A `<...>` helyőrzőket a saját értékeiddel töltsd ki.

## 1. Kód + függőségek

```powershell
cd C:\
git clone https://github.com/JollyLlama-code/domain-watch.git
cd C:\domain-watch
pip install -r requirements.txt
Remove-Item seen.json -ErrorAction SilentlyContinue   # TISZTA START: az elso futas csendben seedel, nincs ertesites-cunami
```

> A repóban commitolt `seen.json` ~11k régi bejegyzést tartalmaz; ezt
> kötelező törölni, különben az első futás nem seed-skippel. A `check.py`
> az első futáskor újra létrehozza (üresből seedelve). NE commitold vissza
> a törlést — csak helyben, a szerveren.

> Ha a `C:\` gyökérbe írás elakad (Defender/NTFS védelem), klónozz a
> `$env:USERPROFILE`-ba és add meg a `-DwDir`-t a task-scriptnek.

## 2. Titkok (NEM a repóból — frissen)

Hozd létre `C:\domain-watch\secrets.env`:

```
MICROWARE_API_PASSWORD=<a microware API jelszo, NEM a login jelszo>
BACKORDER_HMAC_SECRET=<uj veletlen 32+ hex; generald: python -c "import secrets;print(secrets.token_hex(32))">
```

> A `gittoken.txt`-t és a régi `secrets.env`-t NE másold át az LPNTY-ről.

## 3. cloudflared tunnel

```powershell
cloudflared tunnel login                       # böngészőben hagyd jóvá babakocsiszakaruhaz.hu-ra
cloudflared tunnel create domain-watch-backorder
python make_cf_config.py                        # config.yml -> backorder.babakocsiszakaruhaz.hu:8000
cloudflared tunnel route dns domain-watch-backorder backorder.babakocsiszakaruhaz.hu
```

> A Cloudflare DNS-ben a `backorder.babakocsiszakaruhaz.hu` CNAME most az új tunnelre
> mutat. Ha a régi LPNTY route ütközne, töröld azt (lásd 7. lépés).

## 4. Taskok regisztrálása

Derítsd ki az értékeket, majd futtasd:

```powershell
(Get-Command python).Source     # ezt add -PyExe-nek
whoami /user                     # a SID oszlopot add -AdminSid-nek (vagy hagyd ki: a current user lesz)

.\register_server_tasks.ps1 -PyExe "<python.exe utvonal>" -DwDir "C:\domain-watch"
```

Várt: mindhárom task `state=Ready/Running`, `lastResult=0` vagy `267009` (fut).

## 5. Smoke a tunnelen

```powershell
python smoke_lpnty.py https://backorder.babakocsiszakaruhaz.hu
```

Ezután állítsd be a `config.json`-ban:
- `backorder.tunnel_url` = `https://backorder.babakocsiszakaruhaz.hu/backorder`
- `backorder.enabled` = `true`, `backorder.dry_run` = `true` (még NEM élesítünk)

## 6. Microware whitelist csere

`admin.microware.hu` → Beállítások → API hozzáférés beállítása:
- Vedd fel a szerver aktuális publikus IP-jét (az IP-őr első ntfy push-a
  megmondja, vagy: `Invoke-WebRequest api.ipify.org`).
- Töröld a régi `178.48.104.118` (LPNTY) IP-t.

## 7. LPNTY leszerelés (a haver gépén)

```powershell
Unregister-ScheduledTask -TaskName "DomainWatch","BackorderAPI","CloudflaredTunnel" -Confirm:$false
cloudflared tunnel route dns --overwrite-dns domain-watch-backorder backorder.babakocsiszakaruhaz.hu   # ha a régi route maradt volna
```

> Ha a régi tunnelt teljesen meg akarod szüntetni: `cloudflared tunnel delete <regi-tunnel-id>` az LPNTY-n.

## 8. Dry-run éles smoke (nincs költés)

A telefonon kapj egy match push-t (vagy `python send_test_push.py`), nyomd meg
a **Backorder** gombot. A `dry_run.log`-ban megjelenik a teljes microware
request body, valós hívás és költés nélkül. Ha eddig minden zöld, a költözés
kész.

## Élesítés (KÉSŐBB, külön döntés)

`dry_run:false` + alacsony értékű első célpont — ez NEM része a költözésnek.
