# -*- coding: utf-8 -*-
"""Protecoes operacionais para evitar duplicidade e videos ruins."""

import json
import math
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests as _requests
except ImportError:
    _requests = None

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "postagens_realizadas.json"


def _carregar_estado() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = STATE_FILE.with_suffix(".json.corrompido")
        STATE_FILE.replace(backup)
        return {}


def _salvar_estado(estado: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def chave_postagem(data_iso: str, tipo: str, rede: str, arquivo: str) -> str:
    return f"{data_iso}|{tipo}|{rede}|{arquivo}"


def ja_publicado_instagram_api(data_iso: str) -> bool:
    """Verifica na API do Instagram se já existe post publicado hoje.
    Proteção contra Railway apagar postagens_realizadas.json no restart."""
    if _requests is None:
        return False
    ig_token   = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    ig_user_id = os.getenv("INSTAGRAM_USER_ID")
    if not ig_token or not ig_user_id:
        return False
    try:
        r = _requests.get(
            f"https://graph.instagram.com/v19.0/{ig_user_id}/media",
            params={"fields": "timestamp", "limit": 10, "access_token": ig_token},
            timeout=15,
        )
        r.raise_for_status()
        for media in r.json().get("data", []):
            if media.get("timestamp", "").startswith(data_iso):
                return True
    except Exception as e:
        print(f"[Guard] Aviso: não foi possível verificar API Instagram: {e}")
    return False


def ja_publicado_facebook_api(data_iso: str) -> bool:
    """Verifica na API do Facebook se já existe post publicado hoje.
    Proteção contra Railway apagar postagens_realizadas.json no restart."""
    if _requests is None:
        return False
    fb_token   = os.getenv("FACEBOOK_PAGE_TOKEN")
    fb_page_id = os.getenv("FACEBOOK_PAGE_ID")
    if not fb_token or not fb_page_id:
        return False
    try:
        r = _requests.get(
            f"https://graph.facebook.com/v25.0/{fb_page_id}/photos",
            params={"fields": "created_time", "limit": 10, "access_token": fb_token},
            timeout=15,
        )
        r.raise_for_status()
        for post in r.json().get("data", []):
            if post.get("created_time", "").startswith(data_iso):
                return True
    except Exception as e:
        print(f"[Guard] Aviso: não foi possível verificar API Facebook: {e}")
    return False


def ja_publicado(data_iso: str, tipo: str, rede: str, arquivo: str) -> bool:
    estado = _carregar_estado()
    if chave_postagem(data_iso, tipo, rede, arquivo) in estado:
        return True
    if rede == "instagram" and tipo == "foto":
        return ja_publicado_instagram_api(data_iso)
    if rede == "facebook" and tipo == "foto":
        return ja_publicado_facebook_api(data_iso)
    return False


def registrar_publicacao(data_iso: str, tipo: str, rede: str, arquivo: str, post_id: str) -> None:
    estado = _carregar_estado()
    estado[chave_postagem(data_iso, tipo, rede, arquivo)] = {
        "data": data_iso,
        "tipo": tipo,
        "rede": rede,
        "arquivo": arquivo,
        "post_id": post_id,
        "registrado_em": datetime.now().isoformat(timespec="seconds"),
    }
    _salvar_estado(estado)


def validar_audio_video(caminho: Path, min_volume_medio_db: float = -35.0) -> None:
    """Falha se o video nao tiver audio ou se o audio estiver baixo demais."""
    ffprobe_bin = os.getenv("FFPROBE_BIN") or shutil.which("ffprobe")
    ffmpeg_bin = os.getenv("FFMPEG_BIN") or shutil.which("ffmpeg")
    if not ffprobe_bin or not ffmpeg_bin:
        raise RuntimeError(
            "ffmpeg/ffprobe nao encontrado. Instale ffmpeg no servidor ou configure "
            "FFMPEG_BIN e FFPROBE_BIN."
        )

    ffprobe = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(caminho),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if ffprobe.returncode != 0 or "audio" not in ffprobe.stdout:
        raise RuntimeError(f"Video sem trilha de audio: {caminho}")

    volume = subprocess.run(
        [
            ffmpeg_bin,
            "-i",
            str(caminho),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    saida = volume.stderr + volume.stdout
    mean_volume = None
    for linha in saida.splitlines():
        if "mean_volume:" in linha:
            valor = linha.split("mean_volume:", 1)[1].strip().split(" ", 1)[0]
            mean_volume = float(valor)
            break

    if mean_volume is None or math.isinf(mean_volume):
        raise RuntimeError(f"Nao foi possivel medir o volume do video: {caminho}")

    if mean_volume < min_volume_medio_db:
        raise RuntimeError(
            f"Audio baixo demais em {caminho.name}: {mean_volume:.1f} dB "
            f"(minimo recomendado: {min_volume_medio_db:.1f} dB)"
        )
