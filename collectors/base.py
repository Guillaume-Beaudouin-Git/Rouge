"""Socle commun des collecteurs ROUGE.

Chaque collecteur hérite de BaseCollector et implémente collect().
Le socle fournit : fetch HTTP avec backoff exponentiel, cache disque,
écriture Parquet partitionnée par date (idempotente), logging structuré
JSON-lines dans _logs/, et marquage stale en cas d'échec de flux.
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = REPO_ROOT / os.getenv("ROUGE_DATA_DIR", "data")
LOG_DIR = REPO_ROOT / os.getenv("ROUGE_LOG_DIR", "_logs")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """Logger JSON-lines : un fichier par collecteur dans _logs/ + stderr."""
    logger = logging.getLogger(f"rouge.{name}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_DIR / f"{name}.log", encoding="utf-8")
    fh.setFormatter(_JsonFormatter())
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    return logger


class FetchError(RuntimeError):
    """Échec définitif d'un fetch après épuisement des tentatives."""


class BaseCollector(abc.ABC):
    """Contrat commun : idempotent, parquet partitionné, cache, backoff.

    Sous-classes : définir `name` et `dataset`, implémenter collect()
    qui retourne un DataFrame normalisé (une ligne = un enregistrement).
    """

    name: str = "base"
    dataset: str = "base"
    #: TTL du cache disque en secondes (0 = pas de cache)
    cache_ttl: int = 0
    #: nombre max de tentatives HTTP
    max_retries: int = 5
    #: timeout HTTP par requête
    timeout: float = 30.0

    def __init__(self) -> None:
        self.log = get_logger(self.name)
        self.data_dir = DATA_DIR / self.dataset
        self.cache_dir = DATA_DIR / "_cache" / self.name
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------- HTTP

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": "rouge-collector/0.1"},
                follow_redirects=True,
            )
        return self._client

    def fetch(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool | None = None,
    ) -> httpx.Response:
        """GET avec backoff exponentiel + jitter ; respecte Retry-After.

        Réessaie sur erreurs réseau, 429 et 5xx. Lève FetchError au-delà
        de max_retries — l'appelant décide du repli (donnée stale).
        """
        if use_cache is None:
            use_cache = self.cache_ttl > 0
        cache_key = None
        if use_cache:
            cache_key = self._cache_key(url, params)
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.client.get(url, params=params, headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else delay
                    self.log.warning(
                        "http retryable",
                        extra={"ctx": {"url": url, "status": resp.status_code,
                                       "attempt": attempt, "wait_s": round(wait, 1)}},
                    )
                    time.sleep(wait + random.uniform(0, 0.5))
                    delay = min(delay * 2, 60)
                    continue
                resp.raise_for_status()
                if cache_key:
                    self._cache_put(cache_key, resp)
                return resp
            except httpx.HTTPStatusError:
                raise
            except httpx.HTTPError as err:
                last_err = err
                self.log.warning(
                    "http error",
                    extra={"ctx": {"url": url, "attempt": attempt, "err": str(err)}},
                )
                time.sleep(delay + random.uniform(0, 0.5))
                delay = min(delay * 2, 60)
        raise FetchError(f"{self.name}: échec après {self.max_retries} tentatives sur {url}") from last_err

    def fetch_json(self, url: str, **kwargs: Any) -> Any:
        return self.fetch(url, **kwargs).json()

    # ------------------------------------------------------------ cache

    def _cache_key(self, url: str, params: dict[str, Any] | None) -> str:
        raw = url + json.dumps(params or {}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _cache_get(self, key: str) -> httpx.Response | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.log.info("cache hit", extra={"ctx": {"key": key}})
        return httpx.Response(200, content=payload["body"].encode("utf-8"))

    def _cache_put(self, key: str, resp: httpx.Response) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(key).write_text(
            json.dumps({"url": str(resp.url), "body": resp.text}), encoding="utf-8"
        )

    # ---------------------------------------------------------- parquet

    def write_parquet(self, df: pd.DataFrame, partition_date: str | None = None) -> Path:
        """Écrit data/<dataset>/date=YYYY-MM-DD/part.parquet (écrase la
        partition du jour : ré-exécuter le collecteur est idempotent)."""
        if df.empty:
            raise ValueError(f"{self.name}: DataFrame vide, rien à écrire")
        date = partition_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        part_dir = self.data_dir / f"date={date}"
        part_dir.mkdir(parents=True, exist_ok=True)
        out = part_dir / "part.parquet"
        df = df.copy()
        df["_collected_at"] = datetime.now(timezone.utc)
        df.to_parquet(out, index=False)
        self.log.info(
            "parquet written",
            extra={"ctx": {"dataset": self.dataset, "date": date,
                           "rows": len(df), "path": str(out)}},
        )
        return out

    def latest_partition(self) -> Path | None:
        """Dernière partition valide — utilisée pour servir du stale."""
        if not self.data_dir.exists():
            return None
        parts = sorted(self.data_dir.glob("date=*/part.parquet"))
        return parts[-1] if parts else None

    # -------------------------------------------------------------- run

    @abc.abstractmethod
    def collect(self) -> pd.DataFrame:
        """Récupère et normalise les données ; retourne le DataFrame à écrire."""

    def run(self) -> bool:
        """Exécution complète. Retourne False si le flux est tombé (repli
        silencieux : la dernière partition valide reste servie, l'API
        marquera stale=true)."""
        t0 = time.monotonic()
        try:
            df = self.collect()
            self.write_parquet(df)
            self.log.info(
                "run ok",
                extra={"ctx": {"rows": len(df), "dur_s": round(time.monotonic() - t0, 2)}},
            )
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False
        finally:
            if self._client is not None:
                self._client.close()
                self._client = None
