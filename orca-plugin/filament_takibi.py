# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Filament Takibi"
# description = "Filament stoğunu OrcaSlicer içinden takip et; dilimleme sonrası gramı onayla ve düş. Web aracıyla GitHub üzerinden senkronize olur."
# author = "Yusuf Can"
# version = "0.3.1"
# ///
"""Filament Takibi - OrcaSlicer plugin.

Iki capability kaydeder:

* ``Filament Takibi`` (Pages): ana pencerede stok sekmesi.
* ``Filament Takibi - G-code`` (SlicingPipeline): dilimleme sonrası
  G-code altbilgisinden filament kullanımını okur ve onay kuyruğuna ekler.

Veri, eklentinin kendi klasorunde (``orca.host.plugin.storage()``) tutulur.
GitHub modu, web sitesinin kullandığı ``data.json`` dosyasıyla ortak çalışır.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import math
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

import orca

PLUGIN_VERSION = "0.4.2"

SPOOLS_FILE = "spools.json"
PENDING_FILE = "pending.json"
SETTINGS_FILE = "settings.json"
BACKUP_DIR = "backup"
BACKUP_KEEP = 10

MAX_LOG = 100
MAX_SPOOLS = 2000
MAX_PENDING = 200
# Yeniden gonderimde (ayni dosya) uyari cikmasin: son bu kadar ms icinde
# uygulanmis kayit varsa "kayit olusmadi" uyarisi verilmez.
RECENT_APPLY_MS = 120000
MAX_MAPPINGS = 200
MAX_PROCESS_MAP = 20

CSV_COLS = [
    "id", "marka", "malzeme", "renk", "renk_kodu",
    "filament_g", "kalan_g", "bos_makara_g", "fiyat", "not",
]

DEFAULT_SETTINGS = {
    "github": {
        "owner": "mryusufcan",
        "repo": "filament-takibi",
        "token": "",
        "branch": "main",
        "path": "data.json",
    },
    "auto_apply": False,
    # Filament on ayari secilince islem profilini de degistir. Esleme
    # Ayarlar'da tutulur: eklenti Orca'nin islem profillerini listeleyemiyor.
    "auto_process": True,
    "process_map": {"PLA": "PLA 0.20mm ---", "ABS": "ABS 0.20mm ---"},
    "last_sync": 0,
    "mappings": {},
}

_LOCK = threading.RLock()
_STORAGE = None


# ---------------------------------------------------------------------------
# Depolama
# ---------------------------------------------------------------------------

def _storage() -> str:
    """Eklentiye ayrılmış klasör. Orca bunu data_dir altında verir."""
    global _STORAGE
    if _STORAGE is None:
        try:
            _STORAGE = str(orca.host.plugin.storage())
        except Exception:
            # Geliştirme / eski sürüm yedeği: yine de çalışsın.
            _STORAGE = os.path.join(os.path.expanduser("~"), ".filament-takibi-orca")
        os.makedirs(_STORAGE, exist_ok=True)
    return _STORAGE


def _path(name: str) -> str:
    return os.path.join(_storage(), name)


def _read_json(name: str, default):
    with _LOCK:
        try:
            with open(_path(name), "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return default


def _write_json(name: str, value) -> None:
    with _LOCK:
        if name == SPOOLS_FILE:
            _backup_spools()
        target = _path(name)
        temp = target + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        os.replace(temp, target)


def _backup_spools() -> None:
    """Uzerine yazmadan once mevcut spools.json'i zaman damgali kopyala.

    Yanlis silme / bozuk kayit durumunda elle geri donus icin son
    ``BACKUP_KEEP`` dosya tutulur. Hata yedeklemeyi asla engellemez.
    """
    try:
        if not os.path.exists(_path(SPOOLS_FILE)):
            return
        folder = _path(BACKUP_DIR)
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = os.path.join(folder, "spools-" + stamp + ".json")
        with open(_path(SPOOLS_FILE), "rb") as src:
            data = src.read()
        with open(target, "wb") as dst:
            dst.write(data)
        backups = sorted(
            entry for entry in os.listdir(folder)
            if entry.startswith("spools-") and entry.endswith(".json")
        )
        for stale in backups[:-BACKUP_KEEP]:
            try:
                os.remove(os.path.join(folder, stale))
            except OSError:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Doğrulama / normalizasyon
# ---------------------------------------------------------------------------

def _num(value, default=0.0, low=None, high=None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    if low is not None:
        number = max(low, number)
    if high is not None:
        number = min(high, number)
    return number


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _hex(value) -> str:
    text = str(value or "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", text):
        text = "#" + "".join(ch * 2 for ch in text[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", text):
        return text
    return "#888888"


def _safe_id(value) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_\-.~:@+]{1,100}", text):
        return text
    return "s" + uuid.uuid4().hex[:12]


def _norm(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.replace("ı", "i").replace("İ", "i")


def _sanitize_log(raw) -> list:
    entries = []
    if not isinstance(raw, list):
        return entries
    for entry in raw[-MAX_LOG:]:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("k")
        if kind not in ("use", "weigh", "add", "edit"):
            continue
        stamp = int(_num(entry.get("t"), 0))
        grams = round(_num(entry.get("g"), 0), 1)
        remaining = round(_num(entry.get("r"), 0), 1)
        if stamp <= 0:
            continue
        entries.append({"t": stamp, "k": kind, "g": grams, "r": remaining})
    return entries


def _sanitize_spool(raw, seen: set):
    if not isinstance(raw, dict):
        return None
    spool_id = _safe_id(raw.get("id"))
    while spool_id in seen:
        spool_id = "s" + uuid.uuid4().hex[:12]
    seen.add(spool_id)

    initial = round(_num(raw.get("initial"), 1000, 1, 100000), 1)
    remaining = round(_num(raw.get("remaining"), initial, 0, initial), 1)
    created = int(_num(raw.get("createdAt"), 0)) or int(time.time() * 1000)

    return {
        "id": spool_id,
        "brand": _text(raw.get("brand"), 40),
        "material": _text(raw.get("material"), 20) or "Diğer",
        "colorName": _text(raw.get("colorName"), 40),
        "colorHex": _hex(raw.get("colorHex")),
        "initial": initial,
        "remaining": remaining,
        "spoolWeight": round(_num(raw.get("spoolWeight"), 0, 0, 10000), 1),
        "price": round(_num(raw.get("price"), 0, 0, 1000000), 2),
        "note": _text(raw.get("note"), 200),
        "createdAt": created,
        "log": _sanitize_log(raw.get("log")),
    }


def _sanitize_spools(raw) -> list:
    if not isinstance(raw, list):
        return []
    seen = set()
    spools = []
    for item in raw[:MAX_SPOOLS]:
        spool = _sanitize_spool(item, seen)
        if spool is not None:
            spools.append(spool)
    return spools


# ---------------------------------------------------------------------------
# Ayarlar
# ---------------------------------------------------------------------------

def _sanitize_process_map(raw) -> dict:
    """``{"PLA": "PLA 0.20mm ---"}`` -> temizlenmis esleme.

    Bos hedef, kaydi silmek anlamina gelir: kullanici Ayarlar'da alani
    bosaltirsa o malzeme icin islem profili degistirilmez.
    """
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, value in list(raw.items())[:MAX_PROCESS_MAP]:
        material = _text(key, 20).upper()
        target = _text(value, 120)
        if material and target:
            result[material] = target
    return result


def _load_settings() -> dict:
    raw = _read_json(SETTINGS_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    github = raw.get("github") if isinstance(raw.get("github"), dict) else {}
    defaults = DEFAULT_SETTINGS["github"]
    raw_map = raw.get("mappings") if isinstance(raw.get("mappings"), dict) else {}
    mappings = {}
    for key, value in list(raw_map.items())[:MAX_MAPPINGS]:
        key_text = _text(key, 160)
        value_text = _text(value, 40)
        if key_text and value_text:
            mappings[key_text] = value_text
    # process_map hic yazilmamissa varsayilan esleme gelir; acikca bos
    # kaydedilmisse (kullanici alanlari temizledi) bos kalir.
    if isinstance(raw.get("process_map"), dict):
        process_map = _sanitize_process_map(raw.get("process_map"))
    else:
        process_map = dict(DEFAULT_SETTINGS["process_map"])
    return {
        "github": {
            "owner": _text(github.get("owner"), 100) or defaults["owner"],
            "repo": _text(github.get("repo"), 100) or defaults["repo"],
            "token": _text(github.get("token"), 500),
            "branch": _text(github.get("branch"), 100) or defaults["branch"],
            "path": _text(github.get("path"), 200) or defaults["path"],
        },
        "auto_apply": bool(raw.get("auto_apply", False)),
        "auto_process": bool(raw.get("auto_process", True)),
        "process_map": process_map,
        "last_sync": int(_num(raw.get("last_sync"), 0)),
        "mappings": mappings,
    }


def _save_settings(settings: dict) -> None:
    _write_json(SETTINGS_FILE, settings)


def _public_settings(settings: dict) -> dict:
    github = settings["github"]
    return {
        "github": {
            "owner": github["owner"],
            "repo": github["repo"],
            "branch": github["branch"],
            "path": github["path"],
            "token_set": bool(github["token"]),
        },
        "auto_apply": bool(settings["auto_apply"]),
        "auto_process": bool(settings["auto_process"]),
        "process_map": dict(settings.get("process_map") or {}),
        "last_sync": int(settings["last_sync"]),
        "mapping_count": len(settings.get("mappings") or {}),
        "version": PLUGIN_VERSION,
    }


def _process_target(preset_name: str, process_map: dict) -> str:
    """``"PLA YAKUT"`` -> ``"PLA 0.20mm ---"``.

    Malzeme, on ayar adinin bir kelimesi olarak aranir: ``PLA YAKUT``,
    ``PLA``, ``Creality Generic PLA @K1-all`` hepsi PLA sayilir. Boylece
    kullanicinin kendi kisa on ayarlari da eslesir.
    """
    text = _norm(preset_name)
    if not text or not isinstance(process_map, dict):
        return ""
    words = set(re.split(r"[^a-z0-9+]+", text))
    for material, target in process_map.items():
        key = _norm(material)
        if not key or not _text(target, 120):
            continue
        if key in words or text.startswith(key + " "):
            return _text(target, 120)
    return ""


def _apply_process_preset(preset_name: str) -> str:
    """Filament on ayarina karsilik gelen islem profilini sec.

    Donus: secilen islem profili adi; yapilmadiysa bos metin. Hata
    yazdirmayi engellemez; yalnizca Python loguna tek satir yazilir
    (ornek: islem profilinde kaydedilmemis duzenleme varken kullanici
    kaydet/vazgec diyalogunu iptal etti).
    """
    try:
        settings = _load_settings()
        if not settings.get("auto_process"):
            return ""
        target = _process_target(preset_name, settings.get("process_map") or {})
        if not target:
            return ""
        process = getattr(
            getattr(getattr(orca, "host", None), "preset", None), "process", None
        )
        begin = getattr(process, "begin_update", None)
        if begin is None:
            return ""
        transaction = begin(preset_name=target)
        commit = getattr(transaction, "commit", None)
        if commit is not None:
            commit()
        return target
    except Exception as exc:
        print("[filament-takibi] islem profili degistirilemedi: " + str(exc))
        return ""


# ---------------------------------------------------------------------------
# G-code okuma
# ---------------------------------------------------------------------------

_RE_USED_G = re.compile(r"^;\s*filament used \[g\]\s*=\s*(.+?)\s*$", re.I)
_RE_USED_MM = re.compile(r"^;\s*filament used \[mm\]\s*=\s*(.+?)\s*$", re.I)
_RE_TOTAL_G = re.compile(r"^;\s*total filament used \[g\]\s*=\s*(.+?)\s*$", re.I)
_RE_TYPE = re.compile(r"^;\s*filament_type\s*=\s*(.+?)\s*$", re.I)
_RE_COLOUR = re.compile(r"^;\s*filament_colou?r\s*=\s*(.+?)\s*$", re.I)
_RE_DENSITY = re.compile(r"^;\s*filament_density\s*=\s*(.+?)\s*$", re.I)
_RE_SETTINGS_ID = re.compile(r"^;\s*filament_settings_id\s*=\s*(.+?)\s*$", re.I)
_RE_VENDOR = re.compile(r"^;\s*filament_vendor\s*=\s*(.+?)\s*$", re.I)


def _split_list(value) -> list:
    if isinstance(value, (list, tuple)):
        return [str(item).strip().strip('"') for item in value if str(item).strip()]
    text = str(value or "").strip().strip('"')
    if not text:
        return []
    if ";" in text:
        parts = text.split(";")
    elif "," in text:
        parts = text.split(",")
    else:
        parts = [text]
    return [part.strip().strip('"') for part in parts if part.strip()]


def _read_gcode_stats(path: str, ctx=None) -> dict:
    """G-code'un başındaki config bloğu + sonundaki istatistikleri oku."""
    head = ""
    tail = ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            head = handle.read(min(size, 512 * 1024)).decode("utf-8", "replace")
            if size > 512 * 1024:
                handle.seek(max(0, size - 512 * 1024))
                tail = handle.read().decode("utf-8", "replace")
    except OSError as exc:
        raise RuntimeError(f"G-code okunamadı: {exc}") from exc

    used_g = []
    used_mm = []
    types = []
    colours = []
    densities = []
    settings_ids = []
    vendors = []
    total = 0.0

    for line in (head + "\n" + tail).splitlines():
        match = _RE_USED_G.match(line)
        if match:
            used_g = [_num(part, 0) for part in _split_list(match.group(1))]
            continue
        match = _RE_USED_MM.match(line)
        if match:
            used_mm = [_num(part, 0) for part in _split_list(match.group(1))]
            continue
        match = _RE_TOTAL_G.match(line)
        if match:
            total = _num(match.group(1), 0)
            continue
        match = _RE_TYPE.match(line)
        if match:
            types = _split_list(match.group(1))
            continue
        match = _RE_COLOUR.match(line)
        if match:
            colours = _split_list(match.group(1))
            continue
        match = _RE_DENSITY.match(line)
        if match:
            densities = [_num(part, 0) for part in _split_list(match.group(1))]
            continue
        match = _RE_SETTINGS_ID.match(line)
        if match:
            settings_ids = _split_list(match.group(1))
            continue
        match = _RE_VENDOR.match(line)
        if match:
            vendors = _split_list(match.group(1))
            continue

    # Bambu / kısaltılmış G-code'larda config bloğu olmayabilir.
    if ctx is not None:
        if not types:
            try:
                types = _split_list(ctx.config_value("filament_type"))
            except Exception:
                pass
        if not colours:
            try:
                colours = _split_list(ctx.config_value("filament_colour"))
            except Exception:
                pass
        if not densities:
            try:
                densities = [_num(part, 0) for part in _split_list(ctx.config_value("filament_density"))]
            except Exception:
                pass

    if not used_g and used_mm:
        used_g = []
        for index, millimetres in enumerate(used_mm):
            density = densities[index] if index < len(densities) and densities[index] > 0 else 1.24
            # 1.75 mm filament kesiti ~2.4052 mm^2
            used_g.append(millimetres * 2.4052 * density / 1000.0)

    items = []
    for index, grams in enumerate(used_g):
        if grams <= 0:
            continue
        items.append({
            "grams": round(grams, 1),
            "material": types[index] if index < len(types) else "",
            "color": colours[index] if index < len(colours) else "",
            "preset": settings_ids[index] if index < len(settings_ids) else "",
            "vendor": vendors[index] if index < len(vendors) else "",
        })

    if not items and total > 0:
        items.append({
            "grams": round(total, 1),
            "material": types[0] if types else "",
            "color": colours[0] if colours else "",
            "preset": settings_ids[0] if settings_ids else "",
            "vendor": vendors[0] if vendors else "",
        })

    computed = sum(item["grams"] for item in items)
    return {
        "items": items,
        "total": round(total if total > 0 else computed, 1),
    }


# ---------------------------------------------------------------------------
# Makara eşleştirme
# ---------------------------------------------------------------------------

def _item_signature(item: dict) -> str:
    """G-code'taki filament imzasi: malzeme|renk|marka|on ayar adi.

    Kullanici bir makara sectiginde bu imza -> makara eslesmesi saklanir;
    ayni imza tekrar geldiginde o makara otomatik secilir.

    On ayar adi imzaya dahil: Orca'da renk kutusu on ayarla degismedigi icin
    farkli makaralar ayni renk koduyla gelebiliyor. On ayar adi olmadan
    "PLA YAKUT" ile "PLA GOLD" ayni imzaya duser ve yanlis makara otomatik
    dusulurdu.
    """
    raw = str(item.get("color") or "").strip()
    colour = _hex(raw) if re.fullmatch(r"#[0-9a-fA-F]{3,8}", raw) else ""
    parts = [_norm(item.get("material")), colour, _norm(item.get("vendor"))]
    preset = _norm(item.get("preset"))
    if preset:
        parts.append(preset)
    return "|".join(parts)


def _match_spools(items: list, spools: list, mappings=None) -> list:
    mappings = mappings or {}
    active = [spool for spool in spools if spool["remaining"] > 0]
    for item in items:
        material = _norm(item.get("material"))
        colour_raw = str(item.get("color") or "").strip()
        colour = _hex(colour_raw) if re.fullmatch(r"#[0-9a-fA-F]{3,8}", colour_raw) else ""
        vendor = _norm(item.get("vendor"))

        # Kullanicinin daha once sectigi makara varsa tahmin yurutme.
        learned_id = mappings.get(_item_signature(item))
        if learned_id:
            known = next((spool for spool in active if spool["id"] == learned_id), None)
            if known is not None:
                item["spoolId"] = known["id"]
                item["confidence"] = "high"
                item["learned"] = True
                item["candidates"] = [known["id"]]
                continue

        scored = []
        preset = _norm(item.get("preset"))
        for spool in active:
            spool_material = _norm(spool["material"])
            if material and spool_material == material:
                material_score = 4
            elif material == "pla" and spool_material in ("pla", "pla+"):
                material_score = 3
            elif material and (material in spool_material or spool_material in material):
                material_score = 1
            else:
                material_score = 0
            score = material_score

            if colour and _hex(spool["colorHex"]) == colour:
                score += 6

            spool_brand = _norm(spool["brand"])
            if vendor and spool_brand and (vendor == spool_brand or vendor in spool_brand or spool_brand in vendor):
                score += 3

            # On ayar adi eslesmesi: kullanici "PLA YAKUT" gibi renk adi iceren
            # bir on ayar sectiyse bu, renk kutusundan daha guvenilir bir sinyal.
            # Markasiz makaralarda marka sarti aranmaz. Markali makaranin markasi
            # ya on ayar adinda ya da G-code'un filament_vendor alaninda gecmeli
            # (on ayarlar artik kisa adli: "PLA SIYAH" + vendor XPLA).
            colour_name = _norm(spool.get("colorName"))
            name_hit = False
            if material_score >= 3 and len(colour_name) >= 3 and preset and colour_name in preset:
                if not spool_brand or spool_brand in preset or spool_brand == vendor:
                    score += 8
                    name_hit = True

            if score:
                scored.append((score, spool, name_hit))

        if not scored:
            scored = [(0, spool, False) for spool in active]
        scored.sort(key=lambda pair: (-pair[0], pair[1].get("createdAt", 0)))

        best_score = scored[0][0] if scored else 0
        best = [entry for entry in scored if entry[0] == best_score]
        # Yalnizca malzeme eslesmesi (4 puan) otomatik dusum icin yeterince kesin
        # degil: ayni malzemeden farkli renkler olabilir. Renk (6),
        # malzeme+marka (7) veya on ayar adi (12) eslesmesi "kesin" sayilir.
        if len(best) == 1 and best_score >= 6:
            item["spoolId"] = best[0][1]["id"]
            item["confidence"] = "high"
            if best[0][2]:
                item["presetMatch"] = True
        else:
            item["spoolId"] = best[0][1]["id"] if best else None
            item["confidence"] = "low"
        item["candidates"] = [entry[1]["id"] for entry in scored[:8]]

    return items


# ---------------------------------------------------------------------------
# Onay kuyruğu
# ---------------------------------------------------------------------------

def _load_pending() -> list:
    raw = _read_json(PENDING_FILE, [])
    return raw if isinstance(raw, list) else []


def _save_pending(pending: list) -> None:
    _write_json(PENDING_FILE, pending[-MAX_PENDING:])


_PLATE_NAME_RE = re.compile(r"^\.\d+\.(\d+)\.3mf$", re.IGNORECASE)


def _pending_label(output: str, send: bool) -> str:
    """Onay kutusunda gorunen ad.

    "Yazdir" ile gonderimde Orca gecici bir dosya adi uretir
    (``.19332.0.3mf``); kullaniciya anlamli bir etiket gosterilir.
    """
    name = os.path.basename(str(output or "").replace("\\", "/"))
    if send:
        match = _PLATE_NAME_RE.match(name)
        if match:
            return "Yazıcıya gönderim · Plaka " + str(int(match.group(1)) + 1)
        return "Yazıcıya gönderim" + (" · " + name if name else "")
    return name or "Baskı"


def _is_send_ctx(ctx, output: str) -> bool:
    """G-code dosyaya mi yazildi, yoksa yaziciya mi gonderiliyor?

    Ag uzerinden gonderimde Orca ``ctx.host`` alanina yazici adini verir
    (dosya disa aktariminda ``File``). Bazi yollarda (``.gcode.3mf`` paketi)
    host ``File`` kalir; o durumda hedef yol Orca'nin gecici klasorundedir.
    """
    host = str(getattr(ctx, "host", "") or "").strip()
    if host and host.lower() != "file":
        return True
    low = str(output or "").replace("\\", "/").lower()
    return "/metadata/." in low or "/orcaslicer_model/" in low


def _new_pending(output: str, gcode: str, items: list, total: float, send: bool = False) -> dict:
    record = {
        "id": "p" + uuid.uuid4().hex[:12],
        "t": int(time.time() * 1000),
        "output": _text(output, 200),
        "gcode": _text(gcode, 500),
        "items": items,
        "total": round(total, 1),
        "status": "pending",
        "source": "send" if send else "export",
        "label": _pending_label(output, send),
    }
    if send:
        # Gram, yukleme basarili olunca (UploadFinished) dusulur.
        record["awaiting_upload"] = True
    return record


def _is_duplicate(pending: list, record: dict, window_ms: int = 120000) -> bool:
    """Ayni dosya kisa sure icinde tekrar disa aktarilirsa ikinci kez kaydetme.

    Durumdan bagimsiz bakilir: ``applied``/``ignored`` bir kaydin hemen ardindan
    ayni G-code yeniden yazilirsa stok ikinci kez dusulmemeli. Yuklemesi
    basarisiz olan kayit sayilmaz; kullanici yeniden gonderebilmelidir.
    """
    for entry in pending:
        if entry.get("output") != record.get("output"):
            continue
        if entry.get("error"):
            continue
        if abs(_num(entry.get("total"), 0) - _num(record.get("total"), 0)) >= 0.05:
            continue
        if abs(record["t"] - int(_num(entry.get("t"), 0))) < window_ms:
            return True
    return False


def _finish_upload(ok: bool, message: str = "", window_ms: int = 900000):
    """Yaziciya gonderim bitti: bekleyen kaydi kesinlestir.

    Yukleme basarisizsa gram **dusulmez**; kayit listede uyariyla kalir ki
    kullanici gorup yok sayabilsin. Basariliysa ve ``auto_apply`` aciksa
    gram simdi dusulur.
    """
    with _LOCK:
        pending = _load_pending()
        now = int(time.time() * 1000)
        record = None
        for entry in pending:
            if entry.get("status") != "pending" or not entry.get("awaiting_upload"):
                continue
            if now - int(_num(entry.get("t"), 0)) > window_ms:
                continue
            record = entry
            break
        if record is None:
            if ok:
                # Islem profilinde "Slicing Pipeline Plugin" secili degilse kanca hic
                # cagrilmaz: kayit olusmaz. Sessiz kalma, kullaniciya soyle.
                # (Ayni dosyayi yeniden gondermek taze kayit uretmez; o durumda sus.)
                recent = any(
                    entry.get("status") == "applied"
                    and now - int(_num(entry.get("t"), 0)) < RECENT_APPLY_MS
                    for entry in pending
                )
                if not recent:
                    _notify_toast(
                        "Bu baskı için filament kaydı oluşmadı. İşlem profilinde "
                        "“Slicing Pipeline Plugin” olarak “Filament Takibi - G-code” seçili mi?"
                    )
            return None

        record.pop("awaiting_upload", None)
        if not ok:
            record["error"] = _text(message or "Yükleme başarısız", 200)
            _save_pending(pending)
            _notify_state()
            return None

        _save_pending(pending)
        settings = _load_settings()
        high = all(
            item.get("confidence") == "high" and item.get("spoolId")
            for item in record.get("items", [])
        )
        if settings["auto_apply"] and high:
            spools = _apply_pending(record["id"], {})
            if spools is not None:
                _notify_state()
                return spools
        _notify_state()
        return None


def _notify_state() -> None:
    if PAGE_REF is None:
        return
    try:
        PAGE_REF.post_message({"type": "state", **_state()})
    except Exception:
        pass


def _notify_toast(text: str) -> None:
    if PAGE_REF is None:
        return
    try:
        PAGE_REF.post_message({"type": "toast", "text": text})
    except Exception:
        pass


def _apply_pending(pending_id: str, assignments=None):
    # Arayuz ve yukleme is parcacigi ayni dosyalara yazabilir; sirayla calissin.
    with _LOCK:
        return _apply_pending_locked(pending_id, assignments)


def _apply_pending_locked(pending_id: str, assignments=None):
    pending = _load_pending()
    record = next((entry for entry in pending if entry.get("id") == pending_id), None)
    if record is None:
        return None

    spools = _sanitize_spools(_read_json(SPOOLS_FILE, []))
    assignments = assignments or {}
    applied = 0
    learned = {}

    for index, item in enumerate(record.get("items", [])):
        assigned = assignments.get(str(index))
        explicit = assigned if isinstance(assigned, str) and assigned else None
        spool_id = explicit or item.get("spoolId")
        if not spool_id:
            continue
        spool = next((entry for entry in spools if entry["id"] == spool_id), None)
        if spool is None:
            continue
        grams = min(round(_num(item.get("grams"), 0), 1), spool["remaining"])
        if grams <= 0:
            continue
        remaining = round(max(0, spool["remaining"] - grams), 1)
        spool["remaining"] = remaining
        log = (spool.get("log") or [])[-(MAX_LOG - 1):]
        log.append({"t": int(time.time() * 1000), "k": "use", "g": grams, "r": remaining})
        spool["log"] = log
        applied += 1
        # Kullanici bu makarayi elle sectiyse eslesmeyi hatirla.
        if explicit:
            signature = _item_signature(item)
            if signature.strip("|"):
                learned[signature] = spool_id

    if applied == 0:
        return None

    _write_json(SPOOLS_FILE, spools)
    record["status"] = "applied"
    record["appliedAt"] = int(time.time() * 1000)
    _save_pending(pending)

    if learned:
        settings = _load_settings()
        mappings = settings.get("mappings") or {}
        mappings.update(learned)
        if len(mappings) > MAX_MAPPINGS:
            for stale in list(mappings)[: len(mappings) - MAX_MAPPINGS]:
                mappings.pop(stale, None)
        settings["mappings"] = mappings
        _save_settings(settings)

    return spools


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _csv_cell(value, textual: bool = False) -> str:
    text = "" if value is None else str(value)
    if textual and re.match(r"^[=+\-@\t\r]", text):
        text = "'" + text
    if any(char in text for char in ';",\n\r'):
        text = '"' + text.replace('"', '""') + '"'
    return text


def _to_csv(spools: list) -> str:
    lines = [";".join(CSV_COLS)]
    for spool in spools:
        lines.append(";".join([
            _csv_cell(spool["id"]),
            _csv_cell(spool["brand"], True),
            _csv_cell(spool["material"], True),
            _csv_cell(spool["colorName"], True),
            _csv_cell(spool["colorHex"]),
            _csv_cell(spool["initial"]),
            _csv_cell(spool["remaining"]),
            _csv_cell(spool["spoolWeight"]),
            _csv_cell(spool["price"]),
            _csv_cell(spool["note"], True),
        ]))
    return "\ufeff" + "\r\n".join(lines) + "\r\n"


def _export_csv(spools: list) -> str:
    name = "filament-yedek-" + datetime.now().strftime("%Y-%m-%d") + ".csv"
    target = _path(name)
    with _LOCK:
        with open(target, "w", encoding="utf-8", newline="") as handle:
            handle.write(_to_csv(spools))
    return target


def _parse_csv(text: str) -> list:
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines:
        return []
    delimiter = ";" if lines[0].count(";") >= lines[0].count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if len(rows) < 2:
        return []

    header = [cell.strip().lower() for cell in rows[0]]

    def column(row, name):
        try:
            index = header.index(name)
        except ValueError:
            return ""
        return row[index] if index < len(row) else ""

    def number(value, default):
        try:
            return float(str(value).replace(",", "."))
        except ValueError:
            return default

    def textual(value, limit):
        value = str(value or "").strip()
        if re.match(r"^'[=+\-@\t\r]", value):
            value = value[1:]
        return value[:limit]

    spools = []
    for row in rows[1:501]:
        initial = max(1.0, number(column(row, "filament_g"), 1000))
        remaining_raw = column(row, "kalan_g").strip()
        remaining = initial if not remaining_raw else min(initial, max(0.0, number(remaining_raw, initial)))
        spool_id = column(row, "id").strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-.~:@+]{1,100}", spool_id):
            spool_id = "s" + uuid.uuid4().hex[:12]
        spools.append({
            "id": spool_id,
            "brand": textual(column(row, "marka"), 40),
            "material": textual(column(row, "malzeme"), 20) or "Diğer",
            "colorName": textual(column(row, "renk"), 40),
            "colorHex": _hex(column(row, "renk_kodu")),
            "initial": round(initial, 1),
            "remaining": round(remaining, 1),
            "spoolWeight": round(max(0.0, number(column(row, "bos_makara_g"), 0)), 1),
            "price": round(max(0.0, number(column(row, "fiyat"), 0)), 2),
            "note": textual(column(row, "not"), 200),
            "createdAt": int(time.time() * 1000),
            "log": [],
        })
    return spools


def _merge_spools(local: list, incoming: list) -> list:
    by_id = {spool["id"]: spool for spool in local}
    for spool in incoming:
        old = by_id.get(spool["id"])
        if old is not None:
            logs = {json.dumps(entry, sort_keys=True): entry for entry in old.get("log", [])}
            for entry in spool.get("log", []):
                logs[json.dumps(entry, sort_keys=True)] = entry
            merged_log = sorted(logs.values(), key=lambda entry: entry["t"])[-MAX_LOG:]
            latest_old = max([entry["t"] for entry in old.get("log", [])] or [0])
            latest_new = max([entry["t"] for entry in spool.get("log", [])] or [0])
            base = dict(old if latest_old >= latest_new else spool)
            base["log"] = merged_log
            if merged_log:
                base["remaining"] = merged_log[-1]["r"]
            base["createdAt"] = old.get("createdAt") or spool.get("createdAt") or int(time.time() * 1000)
            by_id[spool["id"]] = base
        else:
            by_id[spool["id"]] = spool
    return sorted(by_id.values(), key=lambda spool: spool.get("createdAt", 0))


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def _github_url(github: dict) -> str:
    owner = urllib.parse.quote(github["owner"], safe="")
    repo = urllib.parse.quote(github["repo"], safe="")
    path = urllib.parse.quote(github["path"].lstrip("/"), safe="/")
    return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"


def _github_request(method: str, url: str, token: str, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", "FilamentTakibi-Orca")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            text = response.read().decode("utf-8", "replace")
            return response.status, (json.loads(text) if text else {})
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(text)
        except ValueError:
            return exc.code, {"message": text}
    except Exception as exc:
        return 0, {"message": f"Ağ hatası: {exc}"}


def _github_pull():
    settings = _load_settings()
    github = settings["github"]
    if not (github["owner"] and github["repo"] and github["token"]):
        return False, "GitHub bilgileri eksik"

    url = _github_url(github) + "?ref=" + urllib.parse.quote(github["branch"], safe="")
    status, payload = _github_request("GET", url, github["token"])
    if status == 404:
        return False, "GitHub'da dosya yok; önce 'GitHub'a gönder'"
    if status != 200:
        return False, "GitHub hatası: " + str(payload.get("message") or status)

    try:
        content = base64.b64decode(payload.get("content", "")).decode("utf-8")
        remote = _sanitize_spools(json.loads(content))
    except Exception as exc:
        return False, f"Dosya okunamadı: {exc}"

    local = _sanitize_spools(_read_json(SPOOLS_FILE, []))
    merged = _merge_spools(local, remote)
    _write_json(SPOOLS_FILE, merged)
    settings["last_sync"] = int(time.time() * 1000)
    _save_settings(settings)
    return True, f"GitHub'dan {len(remote)} makara çekildi; toplam {len(merged)}"


def _github_push():
    settings = _load_settings()
    github = settings["github"]
    if not (github["owner"] and github["repo"] and github["token"]):
        return False, "GitHub bilgileri eksik"

    spools = _sanitize_spools(_read_json(SPOOLS_FILE, []))
    text = json.dumps(spools, ensure_ascii=False, indent=2)
    url = _github_url(github)
    query = "?ref=" + urllib.parse.quote(github["branch"], safe="")

    status, payload = _github_request("GET", url + query, github["token"])
    if status == 200 and payload.get("sha"):
        sha = payload["sha"]
    elif status == 404:
        sha = None
    else:
        return False, "GitHub hatası: " + str(payload.get("message") or status)

    body = {
        "message": "Filament Takibi (OrcaSlicer) güncellemesi",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": github["branch"],
    }
    if sha:
        body["sha"] = sha

    status, payload = _github_request("PUT", url, github["token"], body)
    if status == 409:
        # Başka bir cihaz araya girdi: taze sha ile bir kez daha dene.
        status, latest = _github_request("GET", url + query, github["token"])
        if status == 200 and latest.get("sha"):
            body["sha"] = latest["sha"]
            status, payload = _github_request("PUT", url, github["token"], body)

    if status not in (200, 201):
        return False, "GitHub hatası: " + str(payload.get("message") or status)

    settings["last_sync"] = int(time.time() * 1000)
    _save_settings(settings)
    return True, f"GitHub'a {len(spools)} makara gönderildi"


# ---------------------------------------------------------------------------
# Durum
# ---------------------------------------------------------------------------

def _state() -> dict:
    spools = _sanitize_spools(_read_json(SPOOLS_FILE, []))
    pending = [entry for entry in _load_pending() if entry.get("status") == "pending"]
    settings = _load_settings()
    return {
        "spools": spools,
        "pending": pending,
        "settings": _public_settings(settings),
    }


# ---------------------------------------------------------------------------
# Pages capability
# ---------------------------------------------------------------------------

PAGE_REF = None


class FilamentTakibiPage(orca.pages.PagesPluginCapabilityBase):
    def get_name(self):
        return "Filament Takibi"

    def get_ui(self):
        return PAGE_HTML

    def on_load(self):
        global PAGE_REF
        PAGE_REF = self

    def on_unload(self):
        global PAGE_REF
        if PAGE_REF is self:
            PAGE_REF = None

    def on_lifecycle_event(self, event, ctx):
        """Filament on ayari secilince islem profilini de degistir.

        Yalnizca ``PresetSelected`` + ``msg == "filament"`` islenir. Islem
        profilinin kendi secimi ``msg == "print"`` ile gelir ve yoksayilir;
        boylece sonsuz dongu olusmaz. Hata yazdirmayi engellemez.
        """
        try:
            selected = getattr(
                getattr(orca, "LifecycleEvent", None), "PresetSelected", None
            )
            if selected is None or event != selected:
                return
            if _norm(getattr(ctx, "msg", "")) != "filament":
                return
            _apply_process_preset(str(getattr(ctx, "name", "") or ""))
        except Exception:
            try:
                import traceback

                traceback.print_exc()
            except Exception:
                pass

    def on_message(self, data):
        if not isinstance(data, dict):
            return
        kind = data.get("type")
        try:
            if kind == "ready":
                self.post_message({"type": "state", **_state()})

            elif kind == "save":
                spools = _sanitize_spools(data.get("spools"))
                _write_json(SPOOLS_FILE, spools)
                self.post_message({"type": "saved", "count": len(spools)})

            elif kind == "apply_pending":
                spools = _apply_pending(data.get("id"), data.get("assignments") or {})
                if spools is None:
                    self.post_message({"type": "toast", "text": "Düşülecek makara bulunamadı"})
                else:
                    self.post_message({"type": "state", **_state()})
                    self.post_message({"type": "toast", "text": "Filament düşüldü"})

            elif kind == "ignore_pending":
                pending = _load_pending()
                for entry in pending:
                    if entry.get("id") == data.get("id"):
                        entry["status"] = "ignored"
                _save_pending(pending)
                self.post_message({"type": "state", **_state()})

            elif kind == "github_save":
                settings = _load_settings()
                github = settings["github"]
                config = data.get("config") or {}
                defaults = DEFAULT_SETTINGS["github"]
                for key in ("owner", "repo"):
                    if key in config:
                        github[key] = _text(config.get(key), 100) or defaults[key]
                if "branch" in config:
                    github["branch"] = _text(config.get("branch"), 100) or defaults["branch"]
                if "path" in config:
                    github["path"] = _text(config.get("path"), 200) or defaults["path"]
                if config.get("token"):
                    github["token"] = _text(config.get("token"), 500)
                if "auto_apply" in config:
                    settings["auto_apply"] = bool(config.get("auto_apply"))
                if "auto_process" in config:
                    settings["auto_process"] = bool(config.get("auto_process"))
                if isinstance(config.get("process_map"), dict):
                    settings["process_map"] = _sanitize_process_map(
                        config.get("process_map")
                    )
                _save_settings(settings)
                self.post_message({"type": "settings", "settings": _public_settings(settings)})
                self.post_message({"type": "toast", "text": "Ayarlar kaydedildi"})

            elif kind == "github_clear_token":
                settings = _load_settings()
                settings["github"]["token"] = ""
                _save_settings(settings)
                self.post_message({"type": "settings", "settings": _public_settings(settings)})
                self.post_message({"type": "toast", "text": "GitHub token'ı silindi"})

            elif kind == "clear_mappings":
                settings = _load_settings()
                settings["mappings"] = {}
                _save_settings(settings)
                self.post_message({"type": "settings", "settings": _public_settings(settings)})
                self.post_message({"type": "toast", "text": "Öğrenilen eşleşmeler silindi"})

            elif kind == "github_pull":
                _ok, message = _github_pull()
                self.post_message({"type": "state", **_state()})
                self.post_message({"type": "toast", "text": message})

            elif kind == "github_push":
                _ok, message = _github_push()
                self.post_message({"type": "state", **_state()})
                self.post_message({"type": "toast", "text": message})

            elif kind == "export_csv":
                spools = _sanitize_spools(_read_json(SPOOLS_FILE, []))
                target = _export_csv(spools)
                self.post_message({"type": "toast", "text": "Yedek kaydedildi: " + target})

            elif kind == "import_csv":
                incoming = _parse_csv(str(data.get("text") or ""))
                local = _sanitize_spools(_read_json(SPOOLS_FILE, []))
                merged = _merge_spools(local, incoming)
                _write_json(SPOOLS_FILE, merged)
                self.post_message({"type": "state", **_state()})
                self.post_message({"type": "toast", "text": f"{len(incoming)} makara içe aktarıldı"})

            elif kind == "import_json":
                incoming = _sanitize_spools(json.loads(str(data.get("text") or "[]")))
                local = _sanitize_spools(_read_json(SPOOLS_FILE, []))
                merged = _merge_spools(local, incoming)
                _write_json(SPOOLS_FILE, merged)
                self.post_message({"type": "state", **_state()})
                self.post_message({"type": "toast", "text": f"{len(incoming)} makara içe aktarıldı"})

        except Exception as exc:
            try:
                self.post_message({"type": "toast", "text": "Hata: " + str(exc)})
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Slicing pipeline capability
# ---------------------------------------------------------------------------

class FilamentTakibiGCode(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Filament Takibi - G-code"

    def execute(self, ctx):
        try:
            if ctx.step != orca.slicing.Step.psGCodePostProcess:
                return orca.ExecutionResult.success()

            path = str(ctx.gcode_path or "")
            if not path or not os.path.isfile(path):
                return orca.ExecutionResult.skipped("G-code yolu yok")

            stats = _read_gcode_stats(path, ctx)
            if not stats["items"]:
                return orca.ExecutionResult.success("Filament kullanımı bulunamadı")

            spools = _sanitize_spools(_read_json(SPOOLS_FILE, []))
            settings = _load_settings()
            items = _match_spools(stats["items"], spools, settings.get("mappings"))
            output = str(ctx.output_name or "") or os.path.basename(path)
            send = _is_send_ctx(ctx, output)
            record = _new_pending(output, path, items, stats["total"], send=send)

            pending = _load_pending()
            if _is_duplicate(pending, record):
                return orca.ExecutionResult.success("Bu baskı zaten kayıtlı")

            # Ayni cikti icin onceki basarisiz deneme listede kalmasin.
            pending = [
                entry for entry in pending
                if not (entry.get("status") == "pending" and entry.get("error")
                        and entry.get("output") == record["output"])
            ]
            pending.append(record)
            _save_pending(pending)

            if send:
                # Gram, yaziciya gonderim basarili olunca dusulur
                # (UploadFinished olayi). Boylece basarisiz yukleme stogu bozmaz.
                if PAGE_REF is not None:
                    try:
                        PAGE_REF.post_message({"type": "pending", "pending": record})
                    except Exception:
                        pass
                return orca.ExecutionResult.success("Yazıcıya gönderim onayı bekliyor")

            if settings["auto_apply"] and all(
                item.get("confidence") == "high" and item.get("spoolId") for item in items
            ):
                if _apply_pending(record["id"], {}) is not None:
                    if PAGE_REF is not None:
                        try:
                            PAGE_REF.post_message({"type": "state", **_state()})
                        except Exception:
                            pass
                    return orca.ExecutionResult.success("Filament otomatik düşüldü")

            if PAGE_REF is not None:
                try:
                    PAGE_REF.post_message({"type": "pending", "pending": record})
                except Exception:
                    pass
            return orca.ExecutionResult.success("Baskı sonrası onay bekliyor")

        except Exception as exc:
            # Takip hatasi yazdirmayi engellemesin; ayrinti Python loguna gider.
            try:
                import traceback

                traceback.print_exc()
            except Exception:
                pass
            return orca.ExecutionResult.skipped("Filament Takibi atlandı: " + str(exc))

    def on_lifecycle_event(self, event, ctx):
        """Yaziciya gonderim sonucunu izle.

        Basarili yuklemede ``auto_apply`` aciksa gram dusulur; basarisiz
        yuklemede gram dusulmez, kayit uyariyla listede kalir.
        """
        try:
            finished = getattr(getattr(orca, "LifecycleEvent", None), "UploadFinished", None)
            if finished is None or event != finished:
                return
            code = getattr(ctx, "code", None)
            ok_code = getattr(getattr(orca, "LifecycleEvtCode", None), "Ok", None)
            ok = (code == ok_code) if ok_code is not None else str(code).endswith("Ok")
            _finish_upload(bool(ok), str(getattr(ctx, "msg", "") or ""))
        except Exception:
            try:
                import traceback

                traceback.print_exc()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Kayıt
# ---------------------------------------------------------------------------

@orca.plugin
class FilamentTakibiPlugin(orca.base):
    def register_capabilities(self):
        # Not: GitHub erisimi icin burada request_permissions cagrilmaz.
        # Denetim hedefi tam URL oldugundan (owner/repo degisken), ilk
        # "GitHub'dan cek/gonder" isleminde Orca tek seferlik izin sorar.
        orca.register_capability(FilamentTakibiPage)
        orca.register_capability(FilamentTakibiGCode)


PAGE_HTML = r"""<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Filament Takibi</title>
<style>
:root{
  /* Sabit koyu tema: Orca açık temada olsa da sayfa göz almaz. */
  color-scheme:dark;
  --bg:#232322; --fg:#e9e7e2; --muted:#a2a09a;
  --line:#3d3d3a; --accent:#e2683a; --accent-fg:#1b1b19;
  --ok:#4cbf7a; --warn:#e3a92c; --bad:#f07373;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 var(--orca-font,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif)}
.wrap{max-width:1000px;margin:0 auto;padding:18px 16px 44px}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
h1{font-size:20px;margin:0}
.sub{font-size:12px;color:var(--muted);margin-top:2px}
.hdr-actions{display:flex;gap:6px;flex-wrap:wrap}
button{font:inherit;cursor:pointer;border-radius:9px;border:1px solid var(--line);background:transparent;color:inherit;padding:7px 12px}
[hidden]{display:none!important}
button:hover{border-color:var(--muted)}
button.primary{background:var(--accent);color:var(--accent-fg);border-color:var(--accent);font-weight:600}
button.small{padding:5px 9px;font-size:13px}
button.danger{color:var(--bad)}
input,select,textarea{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:7px 9px;width:100%}
input::placeholder,textarea::placeholder{color:#9aa3ad;opacity:1}
.tokenrow{display:flex;gap:6px;align-items:center}
.tokenrow input{flex:1;min-width:0}
.tokenrow button{flex:none;white-space:nowrap}
input[type=color]{padding:2px;height:38px;cursor:pointer}
input[type=checkbox]{width:auto}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.stat{border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.stat b{display:block;font-size:22px}
.stat span{font-size:12px;color:var(--muted)}
.pending{border:1px solid var(--accent);border-radius:12px;padding:12px;margin-bottom:12px}
.pending h3{margin:0 0 8px;font-size:15px}
.pitem{display:grid;grid-template-columns:1.1fr 1.2fr auto;gap:8px;align-items:center;padding:7px 0;border-top:1px solid var(--line);font-size:14px}
.pitem .hint{display:block;margin-top:2px}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
.tools .search{flex:1 1 200px}
.tools .sort{flex:0 1 190px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.chip{border-radius:999px;padding:4px 11px;font-size:13px}
.chip.on{background:var(--accent);color:var(--accent-fg);border-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:10px}
.card{border:1px solid var(--line);border-radius:14px;padding:13px;display:flex;flex-direction:column;gap:9px}
.top{display:flex;gap:10px;align-items:center}
.swatch{width:42px;height:42px;border-radius:50%;border:3px solid var(--line);flex:none}
.ttl{min-width:0;flex:1}
.ttl b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ttl span{font-size:12px;color:var(--muted)}
.badge{font-size:11px;font-weight:600;border-radius:999px;padding:2px 7px;color:#fff;background:var(--bad);white-space:nowrap}
.bar{height:9px;background:var(--line);border-radius:999px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:999px}
.nums{display:flex;justify-content:space-between;align-items:baseline;font-size:13px;color:var(--muted)}
.nums b{color:var(--fg);font-size:19px}
.panel{display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--line);padding-top:9px}
.note{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.note.warn{color:var(--bad);font-weight:600}
.hint{font-size:12px;color:var(--muted)}
.hint.warn{color:var(--bad);font-weight:600}
.use{display:flex;gap:6px}
.use input{flex:1;min-width:0}
.quick{display:flex;gap:5px;flex-wrap:wrap}
.quick button{padding:4px 9px;font-size:12px}
.acts{display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap}
.toggle{width:100%;background:transparent;color:var(--muted)}
.empty{text-align:center;color:var(--muted);padding:36px 12px;border:1px dashed var(--line);border-radius:14px;grid-column:1/-1}
.hist{margin-top:16px;border:1px solid var(--line);border-radius:12px;padding:2px 12px 8px}
.hist summary{cursor:pointer;padding:9px 0;font-weight:600}
.hsum{font-size:12px;color:var(--muted);margin:0 0 6px}
.hday{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;margin:10px 0 3px}
.hrow{display:grid;grid-template-columns:12px 1fr auto;gap:7px;align-items:center;padding:5px 0;border-top:1px solid var(--line);font-size:13px}
.hrow i{width:10px;height:10px;border-radius:50%}
.hd small,.hv small{display:block;font-size:11px;color:var(--muted);font-weight:400}
.hv{font-weight:600;white-space:nowrap;text-align:right}
dialog{border:1px solid var(--line);border-radius:14px;background:var(--bg);color:var(--fg);padding:0;width:min(470px,calc(100% - 20px));max-height:calc(100% - 20px)}
dialog::backdrop{background:rgba(0,0,0,.45)}
form{display:flex;flex-direction:column;gap:10px;padding:16px}
form h2{margin:0;font-size:17px}
label{display:flex;flex-direction:column;gap:3px;font-size:13px;color:var(--muted)}
label.inline{flex-direction:row;align-items:center;gap:8px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.row3{display:grid;grid-template-columns:1fr 100px;gap:8px}
.formacts{display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap;margin-top:2px}
.slist{display:flex;flex-direction:column}
.srow{display:flex;justify-content:space-between;align-items:center;width:100%;background:transparent;border:0;border-top:1px solid var(--line);border-radius:0;padding:8px 2px;font-size:14px;text-align:left}
.snm{color:var(--muted)}
.sv{font-weight:600}
.sv.low{color:var(--bad)}
#toast{position:fixed;left:50%;transform:translateX(-50%);bottom:18px;background:#3a3a37;color:var(--fg);border:1px solid var(--line);padding:8px 14px;border-radius:9px;font-size:13px;display:none;z-index:20;max-width:90%;overflow-wrap:anywhere}
@media(max-width:640px){.stats{grid-template-columns:1fr 1fr}.pitem{grid-template-columns:1fr}.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>🧵 Filament Takibi</h1>
      <div class="sub" id="syncInfo">Orca içi stok yükleniyor…</div>
    </div>
    <div class="hdr-actions">
      <button class="small" id="settingsBtn">Ayarlar</button>
      <button class="primary" id="addBtn">+ Yeni makara</button>
    </div>
  </header>

  <section class="stats">
    <div class="stat"><b id="stCount">0</b><span>Makara</span></div>
    <div class="stat"><b id="stKg">0 kg</b><span>Toplam kalan</span></div>
    <div class="stat"><b id="stLow">0</b><span>Azalan (%20 altı)</span></div>
  </section>

  <section class="pending" id="pendingWrap" style="display:none">
    <h3>Baskı sonrası filament düşümü</h3>
    <div id="pendingList"></div>
  </section>

  <div class="tools">
    <input class="search" id="q" type="search" placeholder="Ara: marka, renk, malzeme…">
    <select class="sort" id="sort">
      <option value="low">Kalan: azdan çoğa</option>
      <option value="high">Kalan: çoktan aza</option>
      <option value="new">En yeni</option>
      <option value="mat">Malzeme</option>
    </select>
  </div>
  <div class="chips" id="chips"></div>
  <div class="grid" id="list"></div>

  <details class="hist" id="hist">
    <summary id="histSum">Geçmiş</summary>
    <div id="histBody"></div>
  </details>
</div>

<dialog id="dlg">
  <form id="form" method="dialog">
    <h2 id="dlgTitle">Yeni makara</h2>
    <div class="row">
      <label>Marka<input id="fBrand" maxlength="40" placeholder="Örn. XPLA"></label>
      <label>Malzeme<select id="fMat">
        <option>PLA</option><option>PLA+</option><option>PETG</option><option>ABS</option>
        <option>ASA</option><option>TPU</option><option>Diğer</option>
      </select></label>
    </div>
    <div class="row3">
      <label>Renk adı<input id="fColor" maxlength="40" placeholder="Örn. Siyah"></label>
      <label>Renk<input id="fHex" type="color" value="#333333"></label>
    </div>
    <div class="row">
      <label>Filament (g)<input id="fInit" type="number" min="1" step="1" value="1000"></label>
      <label>Kalan (g)<input id="fRem" type="number" min="0" step="1" placeholder="Boş = tam"></label>
    </div>
    <details>
      <summary class="hint">Gelişmiş (ağırlık, fiyat, not)</summary>
      <div class="row" style="margin-top:8px">
        <label>Boş makara (g)<input id="fTare" type="number" min="0" step="1" value="200"></label>
        <label>Fiyat<input id="fPrice" type="number" min="0" step="0.01"></label>
      </div>
      <label style="margin-top:8px">Not<input id="fNote" maxlength="200"></label>
    </details>
    <div class="formacts">
      <button type="button" id="cancelBtn">Vazgeç</button>
      <button type="submit" class="primary">Kaydet</button>
    </div>
  </form>
</dialog>

<dialog id="setDlg">
  <form id="setForm" method="dialog">
    <h2>Ayarlar</h2>
    <div class="note">GitHub'daki <b>data.json</b> dosyası web sitesiyle ortak kullanılır. Token yalnızca bu eklentinin klasöründe saklanır.</div>
    <div class="row">
      <label>Kullanıcı<input id="gOwner" placeholder="mryusufcan"></label>
      <label>Depo<input id="gRepo" placeholder="filament-takibi"></label>
    </div>
    <label>Token (fine-grained)
      <span class="tokenrow">
        <input id="gToken" type="password" placeholder="Değiştirmek istemiyorsan boş bırak">
        <button type="button" id="gTokenClear" class="danger" hidden>Token'ı sil</button>
      </span>
    </label>
    <div class="row">
      <label>Dal<input id="gBranch" value="main"></label>
      <label>Dosya<input id="gPath" value="data.json"></label>
    </div>
    <label class="inline"><input id="gAuto" type="checkbox"> Eşleşme kesinse otomatik düş</label>
    <label class="inline"><input id="pAuto" type="checkbox"> Filament seçince işlem profilini de değiştir</label>
    <div class="row">
      <label>PLA işlem profili<input id="pPla" placeholder="PLA 0.20mm ---"></label>
      <label>ABS işlem profili<input id="pAbs" placeholder="ABS 0.20mm ---"></label>
    </div>
    <div class="note">İşlem profili adı, Orca'daki <b>İşlem</b> listesinde göründüğü gibi yazılmalı. Alanı boş bırakırsanız o malzeme için profil değiştirilmez.</div>
    <div class="formacts" id="mapRow" hidden>
      <span class="hint" id="mapInfo"></span>
      <button type="button" id="mapClear" class="danger">Öğrenilen eşleşmeleri sil</button>
    </div>
    <div class="formacts">
      <button type="button" id="pullBtn">GitHub'dan çek</button>
      <button type="button" id="pushBtn">GitHub'a gönder</button>
    </div>
    <div class="formacts">
      <button type="button" id="csvBtn">CSV yedek</button>
      <button type="button" id="csvInBtn">CSV içe al</button>
      <button type="button" id="jsonInBtn">JSON içe al</button>
    </div>
    <div class="formacts">
      <button type="button" id="setClose">Kapat</button>
      <button type="submit" class="primary">Kaydet</button>
    </div>
    <div class="note" id="setVersion"></div>
  </form>
</dialog>

<input type="file" id="fileIn" accept=".csv,.json,text/csv,application/json" style="display:none">
<div id="toast"></div>

<script>
(function(){
  "use strict";

  var state = { spools: [], pending: [], settings: { github: {}, auto_apply: false, last_sync: 0 } };
  var filterMat = "Tümü";
  var openIds = {};
  var editingId = null;
  var fileMode = null;

  function $(id){ return document.getElementById(id); }
  function el(tag, cls, text){ var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function num(v, d){ var n = parseFloat(v); return isFinite(n) ? n : d; }
  function fmt(n){ return (Math.round(n * 10) / 10).toString().replace(".", ","); }
  function fmtW(g){ return g >= 1000 ? fmt(g / 1000) + " kg" : fmt(g) + " g"; }
  function safeHex(h){ return /^#[0-9a-fA-F]{6}$/.test(h || "") ? h : "#888888"; }
  function newId(){ return "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
  function send(m){ if (window.orca && window.orca.postMessage) window.orca.postMessage(m); }
  function toast(msg){ var t = $("toast"); t.textContent = msg; t.style.display = "block"; clearTimeout(toast._t); toast._t = setTimeout(function(){ t.style.display = "none"; }, 2800); }
  function pct(s){ return s.initial > 0 ? Math.max(0, Math.min(100, s.remaining / s.initial * 100)) : 0; }
  function clamp(n){ return Math.max(0, Math.min(100, n)); }

  function withLog(s, kind, grams, rem){
    var log = (s.log || []).concat([{ t: Date.now(), k: kind, g: Math.round(grams * 10) / 10, r: Math.round(rem * 10) / 10 }]);
    return Object.assign({}, s, { log: log.slice(-100) });
  }

  function saveSpool(s){
    var i = state.spools.findIndex(function(x){ return x.id === s.id; });
    if (i >= 0) state.spools[i] = s; else state.spools.push(s);
    send({ type: "save", spools: state.spools });
    renderAll();
  }
  function removeSpool(id){
    state.spools = state.spools.filter(function(x){ return x.id !== id; });
    send({ type: "save", spools: state.spools });
    renderAll();
  }

  function renderAll(){ renderStats(); renderPending(); renderChips(); renderList(); renderHistory(); renderSync(); }

  function renderStats(){
    var total = 0, low = 0;
    state.spools.forEach(function(s){ total += s.remaining; if (pct(s) <= 20) low++; });
    $("stCount").textContent = state.spools.length;
    $("stKg").textContent = fmt(total / 1000) + " kg";
    $("stLow").textContent = low;
  }

  function renderSync(){
    var gh = state.settings.github || {};
    var info = "Yerel stok · " + state.spools.length + " makara";
    if (gh.owner && gh.repo){
      info = "GitHub: " + gh.owner + "/" + gh.repo;
      if (state.settings.last_sync) info += " · son senkron " + new Date(state.settings.last_sync).toLocaleString("tr-TR");
    }
    $("syncInfo").textContent = info;
  }

  function renderPending(){
    var wrap = $("pendingWrap"), list = $("pendingList");
    list.textContent = "";
    var pend = (state.pending || []).filter(function(p){ return p.status === "pending"; });
    if (!pend.length){ wrap.style.display = "none"; return; }
    wrap.style.display = "";
    pend.forEach(function(p){
      var box = el("div");
      box.appendChild(el("div", "note", (p.label || p.output || "Baskı") + " · " + new Date(p.t).toLocaleString("tr-TR") + " · toplam " + fmt(p.total) + " g"));
      if (p.awaiting_upload) box.appendChild(el("div", "note", "Yazıcıya gönderim bitince otomatik düşülecek."));
      if (p.error) box.appendChild(el("div", "note warn", "⚠ Yükleme başarısız, gram düşülmedi: " + p.error));
      var sels = {};
      (p.items || []).forEach(function(item, idx){
        var row = el("div", "pitem");
        var left = el("div");
        left.appendChild(el("b", null, fmt(item.grams) + " g"));
        var hint = (item.material || "") + (item.vendor ? " · " + item.vendor : "") + (item.color ? " · " + item.color : "");
        left.appendChild(el("span", "hint", hint));
        if (item.confidence !== "high") left.appendChild(el("span", "hint warn", "⚠ Eşleşmeyi kontrol et"));
        if (item.learned) left.appendChild(el("span", "hint", "hatırlanan eşleşme"));
        if (item.presetMatch) left.appendChild(el("span", "hint", "ön ayar: " + (item.preset || "")));
        var sel = el("select");
        var active = state.spools.filter(function(s){ return s.remaining > 0; });
        var seen = {};
        var best = [];
        (item.candidates || []).forEach(function(id){
          var s = active.filter(function(x){ return x.id === id; })[0];
          if (s && !seen[s.id]){ seen[s.id] = 1; best.push(s); }
        });
        var rest = active.filter(function(s){ return !seen[s.id]; });
        function addGroup(label, list){
          if (!list.length) return;
          var g = document.createElement("optgroup");
          g.label = label;
          list.forEach(function(s){
            var o = el("option", null, (s.colorName || "Renksiz") + " · " + (s.brand ? s.brand + " " : "") + s.material + " · " + fmt(s.remaining) + " g");
            o.value = s.id; g.appendChild(o);
          });
          sel.appendChild(g);
        }
        addGroup("Önerilen", best);
        addGroup(best.length ? "Diğer makaralar" : "Makaralar", rest);
        var cands = best.concat(rest);
        if (item.spoolId && cands.some(function(s){ return s.id === item.spoolId; })) sel.value = item.spoolId;
        if (!sel.value && cands.length) sel.value = cands[0].id;
        sels[idx] = sel.value;
        sel.onchange = function(){ sels[idx] = sel.value; };
        var right = el("div");
        var apply = el("button", "primary small", "Düş");
        apply.type = "button";
        apply.disabled = !cands.length;
        apply.onclick = function(){ send({ type: "apply_pending", id: p.id, assignments: sels }); };
        right.appendChild(apply);
        row.appendChild(left); row.appendChild(sel); row.appendChild(right);
        box.appendChild(row);
      });
      var acts = el("div", "acts");
      var ign = el("button", "small", "Yoksay"); ign.type = "button";
      ign.onclick = function(){ send({ type: "ignore_pending", id: p.id }); };
      acts.appendChild(ign); box.appendChild(acts);
      list.appendChild(box);
    });
  }

  function renderChips(){
    var mats = ["Tümü"];
    state.spools.forEach(function(s){ if (mats.indexOf(s.material) < 0) mats.push(s.material); });
    if (mats.indexOf(filterMat) < 0) filterMat = "Tümü";
    var host = $("chips"); host.textContent = "";
    if (mats.length <= 2) return;
    mats.forEach(function(m){
      var b = el("button", "chip" + (m === filterMat ? " on" : ""), m);
      b.type = "button";
      b.onclick = function(){ filterMat = m; renderChips(); renderList(); };
      host.appendChild(b);
    });
  }

  function renderList(){
    var q = $("q").value.trim().toLowerCase();
    var view = state.spools.filter(function(s){
      if (filterMat !== "Tümü" && s.material !== filterMat) return false;
      if (!q) return true;
      return ((s.brand || "") + " " + (s.colorName || "") + " " + s.material + " " + (s.note || "")).toLowerCase().indexOf(q) >= 0;
    });
    var map = {}, groups = [];
    view.forEach(function(s){
      var key = s.material + "|" + ((s.colorName || "").trim().toLocaleLowerCase("tr") || "(adsız)");
      var g = map[key];
      if (!g){ g = map[key] = { material: s.material, colorName: (s.colorName || "").trim(), spools: [] }; groups.push(g); }
      g.spools.push(s);
    });
    groups.forEach(function(g){
      g.spools.sort(function(a, b){ return (a.createdAt || 0) - (b.createdAt || 0); });
      g.total = 0; g.initial = 0; g.newest = 0; g.best = g.spools[0];
      g.spools.forEach(function(s){
        g.total += s.remaining; g.initial += s.initial;
        if ((s.createdAt || 0) > g.newest) g.newest = s.createdAt || 0;
        if (s.remaining > g.best.remaining) g.best = s;
      });
      g.total = Math.round(g.total * 10) / 10;
    });
    var mode = $("sort").value;
    groups.sort(function(a, b){
      if (mode === "low") return a.total - b.total;
      if (mode === "high") return b.total - a.total;
      if (mode === "mat") return a.material.localeCompare(b.material, "tr") || a.colorName.localeCompare(b.colorName, "tr");
      return b.newest - a.newest;
    });
    var list = $("list"); list.textContent = "";
    if (!groups.length){
      list.appendChild(el("div", "empty", state.spools.length ? "Sonuç yok." : 'Henüz makara yok. "+ Yeni makara" ile başla veya Ayarlar bölümünden GitHub\'dan çek.'));
      return;
    }
    groups.forEach(function(g){ list.appendChild(card(g)); });
  }

  function card(g){
    var multi = g.spools.length > 1, one = g.spools[0];
    var p = g.initial > 0 ? clamp(g.total / g.initial * 100) : 0;
    var c = el("div", "card");
    var top = el("div", "top");
    var sw = el("div", "swatch"); sw.style.background = safeHex(g.best.colorHex);
    var t = el("div", "ttl");
    t.appendChild(el("b", null, g.colorName || "Renksiz"));
    t.appendChild(el("span", null, multi ? g.material + " · " + g.spools.length + " makara" : (one.brand ? one.brand + " · " : "") + g.material + " · " + fmt(one.initial) + " g"));
    top.appendChild(sw); top.appendChild(t);
    if (g.total <= 0) top.appendChild(el("span", "badge", "Bitti"));
    else if (p <= 20) top.appendChild(el("span", "badge", "Azaldı"));
    c.appendChild(top);

    var bar = el("div", "bar"), fill = el("i");
    fill.style.width = p + "%";
    fill.style.background = p > 50 ? "var(--ok)" : p > 20 ? "var(--warn)" : "var(--bad)";
    bar.appendChild(fill); c.appendChild(bar);

    var nums = el("div", "nums");
    var l = el("span"); l.appendChild(el("b", null, fmtW(g.total))); l.appendChild(document.createTextNode(" kaldı"));
    nums.appendChild(l); nums.appendChild(el("span", null, "%" + Math.round(p)));
    c.appendChild(nums);

    if (multi){
      var slist = el("div", "slist");
      g.spools.forEach(function(s, i){
        var open = !!openIds[s.id];
        var row = el("button", "srow"); row.type = "button";
        row.appendChild(el("span", "snm", (s.brand || "Makara") + " " + (i + 1)));
        row.appendChild(el("span", "sv" + (pct(s) <= 20 ? " low" : ""), fmt(s.remaining) + " g " + (open ? "▴" : "▾")));
        row.onclick = function(){ if (openIds[s.id]) delete openIds[s.id]; else openIds[s.id] = 1; renderList(); };
        slist.appendChild(row);
        if (open) slist.appendChild(spoolPanel(s));
      });
      c.appendChild(slist);
    } else {
      var open = !!openIds[one.id];
      var tog = el("button", "small toggle", open ? "Kapat ▴" : "İşlemler ▾"); tog.type = "button";
      tog.onclick = function(){ if (openIds[one.id]) delete openIds[one.id]; else openIds[one.id] = 1; renderList(); };
      c.appendChild(tog);
      if (open) c.appendChild(spoolPanel(one));
    }
    return c;
  }

  function spoolPanel(s){
    var tare = s.spoolWeight || 0;
    var panel = el("div", "panel");
    panel.appendChild(el("div", "note", (s.brand ? s.brand + " · " : "") + "Makara " + fmt(s.initial) + " g · kalan " + fmt(s.remaining) + " g"));
    if (tare > 0) panel.appendChild(el("div", "note", "Tartıda görünmesi gereken: " + fmt(s.remaining + tare) + " g (boş makara " + fmt(tare) + " g)"));
    if (s.note) panel.appendChild(el("div", "note", s.note));

    var use = el("div", "use");
    var inp = el("input"); inp.type = "number"; inp.min = "0"; inp.step = "1"; inp.placeholder = "Kullandığım gram";
    var ub = el("button", "small", "Kullandım"); ub.type = "button";
    function doUse(){
      var g = num(inp.value, 0);
      if (g <= 0) return;
      var newRem = Math.max(0, Math.round((s.remaining - g) * 10) / 10);
      var used = Math.round((s.remaining - newRem) * 10) / 10;
      if (used <= 0) return;
      delete openIds[s.id];
      saveSpool(withLog(Object.assign({}, s, { remaining: newRem }), "use", used, newRem));
    }
    ub.onclick = doUse;
    inp.onkeydown = function(e){ if (e.key === "Enter"){ e.preventDefault(); doUse(); } };
    panel.appendChild(el("div", "hint", "Kullandığın gramı yaz veya hızlı düğmelere bas:"));
    var quick = el("div", "quick");
    [10, 25, 50, 100].forEach(function(q){
      var qb = el("button", null, "+" + q + " g"); qb.type = "button";
      qb.onclick = function(){ inp.value = Math.round((num(inp.value, 0) + q) * 10) / 10; };
      quick.appendChild(qb);
    });
    panel.appendChild(quick);
    use.appendChild(inp); use.appendChild(ub); panel.appendChild(use);

    var weigh = el("div", "use");
    var winp = el("input"); winp.type = "number"; winp.min = "0"; winp.step = "1"; winp.placeholder = "Tartıdaki toplam (makara dahil)";
    var wb = el("button", "small", "Tartıdan güncelle"); wb.type = "button";
    function doWeigh(){
      var gross = num(winp.value, -1);
      if (gross < 0) return;
      var rem = Math.max(0, Math.min(s.initial, Math.round((gross - tare) * 10) / 10));
      var delta = Math.round((rem - s.remaining) * 10) / 10;
      delete openIds[s.id];
      saveSpool(delta !== 0 ? withLog(Object.assign({}, s, { remaining: rem }), "weigh", delta, rem) : Object.assign({}, s, { remaining: rem }));
    }
    wb.onclick = doWeigh;
    winp.onkeydown = function(e){ if (e.key === "Enter"){ e.preventDefault(); doWeigh(); } };
    panel.appendChild(el("div", "hint", "Ya da makarayı tartıp toplam ağırlığı yaz:"));
    weigh.appendChild(winp); weigh.appendChild(wb); panel.appendChild(weigh);

    var acts = el("div", "acts");
    var eb = el("button", "small", "Düzenle"); eb.type = "button"; eb.onclick = function(){ openDlg(s); };
    var db = el("button", "small danger", "Sil"); db.type = "button";
    var armed = false;
    db.onclick = function(){
      if (!armed){ armed = true; db.textContent = "Emin misin?"; setTimeout(function(){ armed = false; db.textContent = "Sil"; }, 3000); }
      else { delete openIds[s.id]; removeSpool(s.id); }
    };
    acts.appendChild(eb); acts.appendChild(db); panel.appendChild(acts);
    return panel;
  }

  function renderHistory(){
    var all = [];
    state.spools.forEach(function(s){ (s.log || []).forEach(function(e){ all.push({ e: e, s: s }); }); });
    all.sort(function(a, b){ return b.e.t - a.e.t; });
    $("histSum").textContent = "Geçmiş" + (all.length ? " (" + all.length + ")" : "");
    var body = $("histBody"); body.textContent = "";
    if (!all.length){ body.appendChild(el("div", "hsum", "Henüz kayıt yok.")); return; }
    var now = Date.now(), w7 = 0, w30 = 0;
    all.forEach(function(x){
      var e = x.e;
      var used = e.k === "use" ? e.g : (e.k === "weigh" && e.g < 0 ? -e.g : 0);
      if (now - e.t <= 7 * 86400000) w7 += used;
      if (now - e.t <= 30 * 86400000) w30 += used;
    });
    body.appendChild(el("div", "hsum", "Kullanılan: son 7 gün " + fmtW(w7) + " · son 30 gün " + fmtW(w30)));
    var lastDay = "";
    all.slice(0, 60).forEach(function(x){
      var e = x.e, s = x.s;
      var d = new Date(e.t);
      var dk = d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate();
      if (dk !== lastDay){
        body.appendChild(el("div", "hday", d.toLocaleDateString("tr-TR", { day: "numeric", month: "long" })));
        lastDay = dk;
      }
      var row = el("div", "hrow");
      var dot = el("i"); dot.style.background = safeHex(s.colorHex);
      var hd = el("div", "hd");
      hd.appendChild(document.createTextNode((s.colorName || "Renksiz") + " · " + (s.brand ? s.brand + " " : "") + s.material));
      var what = e.k === "use" ? "Kullanıldı" : e.k === "weigh" ? "Tartıdan güncellendi" : e.k === "add" ? "Makara eklendi" : "Elle düzenlendi";
      hd.appendChild(el("small", null, what));
      var hv = el("div", "hv");
      var sign = e.k === "use" ? "-" : (e.g < 0 ? "-" : "+");
      hv.appendChild(document.createTextNode(sign + fmt(Math.abs(e.g)) + " g"));
      hv.appendChild(el("small", null, "kalan " + fmt(e.r) + " g · " + new Date(e.t).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })));
      row.appendChild(dot); row.appendChild(hd); row.appendChild(hv);
      body.appendChild(row);
    });
    if (all.length > 60) body.appendChild(el("div", "hsum", "Son 60 kayıt gösteriliyor."));
  }

  function openDlg(s){
    editingId = s ? s.id : null;
    $("dlgTitle").textContent = s ? "Makarayı düzenle" : "Yeni makara";
    $("fBrand").value = s ? s.brand : "";
    $("fMat").value = s ? s.material : "PLA";
    $("fColor").value = s ? s.colorName : "";
    $("fHex").value = s ? safeHex(s.colorHex) : "#333333";
    $("fInit").value = s ? s.initial : 1000;
    $("fRem").value = s ? s.remaining : "";
    $("fTare").value = s ? (s.spoolWeight || 0) : 200;
    $("fPrice").value = s && s.price ? s.price : "";
    $("fNote").value = s ? s.note : "";
    $("dlg").showModal();
  }

  $("form").onsubmit = function(e){
    e.preventDefault();
    var initial = Math.max(1, num($("fInit").value, 1000));
    var remRaw = $("fRem").value.trim();
    var remaining = remRaw === "" ? initial : Math.max(0, num(remRaw, initial));
    if (remaining > initial) remaining = initial;
    var old = editingId ? state.spools.find(function(x){ return x.id === editingId; }) : null;
    var s = {
      id: editingId || newId(),
      brand: $("fBrand").value.trim(),
      material: $("fMat").value,
      colorName: $("fColor").value.trim(),
      colorHex: safeHex($("fHex").value),
      initial: initial,
      remaining: remaining,
      spoolWeight: Math.max(0, num($("fTare").value, 0)),
      price: Math.max(0, num($("fPrice").value, 0)),
      note: $("fNote").value.trim(),
      createdAt: old && old.createdAt ? old.createdAt : Date.now(),
      log: old && old.log ? old.log : []
    };
    if (!old) s = withLog(s, "add", remaining, remaining);
    else if (old.remaining !== remaining) s = withLog(s, "edit", remaining - old.remaining, remaining);
    saveSpool(s);
    $("dlg").close();
  };

  function syncTokenUi(){
    var gh = (state.settings && state.settings.github) || {};
    $("gToken").placeholder = gh.token_set ? "Kayıtlı token var; değiştirmek istemiyorsan boş bırak" : "ghp_… veya github_pat_…";
    $("gTokenClear").hidden = !gh.token_set;
  }

  function syncMapUi(){
    var count = (state.settings && state.settings.mapping_count) || 0;
    $("mapRow").hidden = count <= 0;
    $("mapInfo").textContent = count > 0 ? count + " öğrenilen eşleşme" : "";
  }

  function syncVersionUi(){
    var ver = (state.settings && state.settings.version) || "";
    $("setVersion").textContent = ver ? "Filament Takibi eklentisi · sürüm " + ver : "";
  }

  function openSettings(){
    var gh = state.settings.github || {};
    $("gOwner").value = gh.owner || "";
    $("gRepo").value = gh.repo || "";
    $("gToken").value = "";
    syncTokenUi();
    $("gBranch").value = gh.branch || "main";
    $("gPath").value = gh.path || "data.json";
    $("gAuto").checked = !!state.settings.auto_apply;
    var pm = state.settings.process_map || {};
    $("pAuto").checked = state.settings.auto_process !== false;
    $("pPla").value = pm.PLA || "";
    $("pAbs").value = pm.ABS || "";
    syncMapUi();
    syncVersionUi();
    $("setDlg").showModal();
  }

  $("settingsBtn").onclick = openSettings;
  $("setClose").onclick = function(){ $("setDlg").close(); };
  $("mapClear").onclick = function(){
    if (!confirm("Öğrenilen eşleşmeler silinsin mi? Makaralar silinmez.")) return;
    send({ type: "clear_mappings" });
  };
  $("gTokenClear").onclick = function(){
    if (!confirm("Kayıtlı GitHub token'ı silinsin mi?")) return;
    $("gToken").value = "";
    send({ type: "github_clear_token" });
  };
  function githubConfig(){
    return {
      owner: $("gOwner").value.trim(), repo: $("gRepo").value.trim(),
      token: $("gToken").value.trim(), branch: $("gBranch").value.trim() || "main",
      path: $("gPath").value.trim() || "data.json", auto_apply: $("gAuto").checked,
      auto_process: $("pAuto").checked,
      process_map: { PLA: $("pPla").value.trim(), ABS: $("pAbs").value.trim() }
    };
  }
  function saveGithub(){
    send({ type: "github_save", config: githubConfig() });
    $("gToken").value = "";
  }
  $("setForm").onsubmit = function(e){
    e.preventDefault();
    saveGithub();
    $("setDlg").close();
  };
  $("pullBtn").onclick = function(){ saveGithub(); send({ type: "github_pull" }); $("setDlg").close(); };
  $("pushBtn").onclick = function(){ saveGithub(); send({ type: "github_push" }); $("setDlg").close(); };
  $("csvBtn").onclick = function(){ send({ type: "export_csv" }); };
  $("csvInBtn").onclick = function(){ fileMode = "csv"; $("fileIn").value = ""; $("fileIn").click(); };
  $("jsonInBtn").onclick = function(){ fileMode = "json"; $("fileIn").value = ""; $("fileIn").click(); };
  $("fileIn").onchange = function(){
    var f = this.files && this.files[0];
    if (!f) return;
    var r = new FileReader();
    r.onload = function(){ send({ type: fileMode === "csv" ? "import_csv" : "import_json", text: String(r.result) }); };
    r.readAsText(f, "utf-8");
    this.value = "";
  };

  $("addBtn").onclick = function(){ openDlg(null); };
  $("cancelBtn").onclick = function(){ $("dlg").close(); };
  $("q").oninput = renderList;
  $("sort").onchange = renderList;

  if (window.orca && window.orca.onMessage){
    window.orca.onMessage(function(msg){
      if (!msg || !msg.type) return;
      if (msg.type === "state"){
        state.spools = msg.spools || [];
        state.pending = msg.pending || [];
        state.settings = msg.settings || state.settings;
        renderAll();
      } else if (msg.type === "pending"){
        state.pending = (state.pending || []).filter(function(p){ return p.id !== msg.pending.id; });
        state.pending.push(msg.pending);
        renderPending();
      } else if (msg.type === "toast"){
        toast(msg.text || "");
      } else if (msg.type === "settings"){
        state.settings = msg.settings || state.settings;
        renderSync();
        if ($("setDlg").open){ syncTokenUi(); syncMapUi(); syncVersionUi(); }
      }
    });
  }
  send({ type: "ready" });
})();
</script>
</body>
</html>
"""
