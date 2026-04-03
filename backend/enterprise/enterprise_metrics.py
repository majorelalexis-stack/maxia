"""MAXIA Enterprise Metrics V12 — Metriques Prometheus-compatible + SLA monitoring

Expose des metriques au format Prometheus text (sans lib prometheus-client) :
- request_count, request_latency_seconds, error_count, active_connections
- Par chain : chain_rpc_latency, chain_rpc_errors, chain_status
- Par service : service_execution_time, service_success_rate
- SLA : uptime_percentage par tier (99%, 99.5%, 99.9%)

Middleware FastAPI pour collecte automatique des metriques HTTP.
"""
import os, time, asyncio
from collections import defaultdict
from typing import Optional
from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["enterprise-metrics"])

# ── Config ──

METRICS_ENABLED = os.getenv("METRICS_ENABLED", "true").lower() == "true"
METRICS_PREFIX = os.getenv("METRICS_PREFIX", "maxia")

# ── Chains supportees (14 chains MAXIA) ──

SUPPORTED_CHAINS = [
    "solana", "base", "ethereum", "xrp", "polygon", "arbitrum",
    "avalanche", "bnb", "ton", "sui", "tron", "near", "aptos", "sei",
]

# ── SLA Tiers et objectifs ──

SLA_TARGETS = {
    "free": {"uptime_target": 99.0, "latency_target_ms": 5000, "description": "Free — 99% uptime"},
    "pro": {"uptime_target": 99.5, "latency_target_ms": 2000, "description": "Pro — 99.5% uptime"},
    "enterprise": {"uptime_target": 99.9, "latency_target_ms": 500, "description": "Enterprise — 99.9% uptime"},
}

# ── Structures de metriques in-memory ──


class Counter:
    """Compteur simple thread-safe (monotoniquement croissant)."""

    __slots__ = ("_value", "_labels")

    def __init__(self):
        self._value: float = 0
        self._labels: dict = defaultdict(float)  # {label_tuple: value}

    def inc(self, amount: float = 1, labels: dict = None):
        if labels:
            key = tuple(sorted(labels.items()))
            self._labels[key] += amount
        else:
            self._value += amount

    def get(self, labels: dict = None) -> float:
        if labels:
            key = tuple(sorted(labels.items()))
            return self._labels.get(key, 0)
        return self._value

    def items(self):
        """Retourne toutes les series (labels_dict, value)."""
        result = []
        if self._value > 0:
            result.append(({}, self._value))
        for key, val in self._labels.items():
            result.append((dict(key), val))
        return result


class Gauge:
    """Jauge (valeur qui peut monter et descendre)."""

    __slots__ = ("_value", "_labels")

    def __init__(self):
        self._value: float = 0
        self._labels: dict = {}  # {label_tuple: value}

    def set(self, value: float, labels: dict = None):
        if labels:
            key = tuple(sorted(labels.items()))
            self._labels[key] = value
        else:
            self._value = value

    def inc(self, amount: float = 1, labels: dict = None):
        if labels:
            key = tuple(sorted(labels.items()))
            self._labels[key] = self._labels.get(key, 0) + amount
        else:
            self._value += amount

    def dec(self, amount: float = 1, labels: dict = None):
        if labels:
            key = tuple(sorted(labels.items()))
            self._labels[key] = self._labels.get(key, 0) - amount
        else:
            self._value -= amount

    def get(self, labels: dict = None) -> float:
        if labels:
            key = tuple(sorted(labels.items()))
            return self._labels.get(key, 0)
        return self._value

    def items(self):
        result = []
        if self._value != 0:
            result.append(({}, self._value))
        for key, val in self._labels.items():
            result.append((dict(key), val))
        return result


class Histogram:
    """Histogramme simplifie (sum, count, buckets) compatible Prometheus."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    __slots__ = ("_sum", "_count", "_buckets", "_bucket_counts", "_labels_data")

    def __init__(self, buckets=None):
        self._buckets = buckets or self.DEFAULT_BUCKETS
        self._sum: float = 0
        self._count: int = 0
        self._bucket_counts: dict = {b: 0 for b in self._buckets}
        self._labels_data: dict = {}  # label_tuple -> {sum, count, bucket_counts}

    def observe(self, value: float, labels: dict = None):
        if labels:
            key = tuple(sorted(labels.items()))
            if key not in self._labels_data:
                self._labels_data[key] = {
                    "sum": 0, "count": 0,
                    "bucket_counts": {b: 0 for b in self._buckets},
                }
            d = self._labels_data[key]
            d["sum"] += value
            d["count"] += 1
            for b in self._buckets:
                if value <= b:
                    d["bucket_counts"][b] += 1
        else:
            self._sum += value
            self._count += 1
            for b in self._buckets:
                if value <= b:
                    self._bucket_counts[b] += 1

    def items(self):
        """Retourne toutes les series."""
        result = []
        if self._count > 0:
            result.append(({}, {"sum": self._sum, "count": self._count, "buckets": dict(self._bucket_counts)}))
        for key, d in self._labels_data.items():
            result.append((dict(key), {"sum": d["sum"], "count": d["count"], "buckets": dict(d["bucket_counts"])}))
        return result


# ── Metriques globales ──

# HTTP
request_count = Counter()
request_latency = Histogram()
error_count = Counter()
active_connections = Gauge()

# Chains RPC
chain_rpc_latency = Histogram(buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0))
chain_rpc_errors = Counter()
chain_status = Gauge()  # 1 = up, 0 = down

# Services
service_execution_time = Histogram()
service_success_count = Counter()
service_failure_count = Counter()

# SLA tracking
_uptime_checks: dict = defaultdict(lambda: {"total": 0, "up": 0})  # chain -> {total, up}
_startup_time = time.time()


# ── Fonctions publiques de recording ──

def record_request(method: str, path: str, status_code: int, duration_seconds: float):
    """Enregistre les metriques d'une requete HTTP."""
    if not METRICS_ENABLED:
        return
    labels = {"method": method, "path": _normalize_path(path), "status": str(status_code)}
    request_count.inc(labels=labels)
    request_latency.observe(duration_seconds, labels=labels)
    if status_code >= 400:
        error_count.inc(labels={"method": method, "status": str(status_code)})


def record_chain_rpc(chain: str, latency_seconds: float, success: bool):
    """Enregistre les metriques d'un appel RPC blockchain."""
    if not METRICS_ENABLED:
        return
    labels = {"chain": chain}
    chain_rpc_latency.observe(latency_seconds, labels=labels)
    if not success:
        chain_rpc_errors.inc(labels=labels)
    # MAJ SLA
    _uptime_checks[chain]["total"] += 1
    if success:
        _uptime_checks[chain]["up"] += 1
    chain_status.set(1.0 if success else 0.0, labels=labels)


def record_service_execution(service_id: str, duration_seconds: float, success: bool):
    """Enregistre les metriques d'execution d'un service."""
    if not METRICS_ENABLED:
        return
    labels = {"service": service_id}
    service_execution_time.observe(duration_seconds, labels=labels)
    if success:
        service_success_count.inc(labels=labels)
    else:
        service_failure_count.inc(labels=labels)


def _normalize_path(path: str) -> str:
    """Normalise le path pour eviter la cardinalite explosive.

    Remplace les UUIDs, hashes et nombres par des placeholders.
    Ex: /api/public/services/abc123 -> /api/public/services/:id
    """
    import re
    # Remplacer les UUIDs
    path = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', ':uuid', path)
    # Remplacer les hex longs (hashes, API keys)
    path = re.sub(r'[0-9a-f]{16,}', ':hash', path)
    # Remplacer les segments purement numeriques
    path = re.sub(r'/\d+(?=/|$)', '/:id', path)
    return path


# ── Generation format texte Prometheus ──

def _format_labels(labels: dict) -> str:
    """Formate les labels Prometheus : {key="value",key2="value2"}."""
    if not labels:
        return ""
    parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _render_counter(name: str, help_text: str, counter: Counter) -> str:
    """Genere le format texte Prometheus pour un Counter."""
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
    for labels, value in counter.items():
        lines.append(f"{name}{_format_labels(labels)} {value}")
    return "\n".join(lines) + "\n"


def _render_gauge(name: str, help_text: str, gauge: Gauge) -> str:
    """Genere le format texte Prometheus pour un Gauge."""
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    for labels, value in gauge.items():
        lines.append(f"{name}{_format_labels(labels)} {value}")
    return "\n".join(lines) + "\n"


def _render_histogram(name: str, help_text: str, histogram: Histogram) -> str:
    """Genere le format texte Prometheus pour un Histogram."""
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
    for labels, data in histogram.items():
        label_str = _format_labels(labels)
        # Buckets
        cumulative = 0
        for bucket in sorted(data["buckets"].keys()):
            cumulative += data["buckets"][bucket]
            bucket_labels = dict(labels) if labels else {}
            bucket_labels["le"] = str(bucket)
            lines.append(f"{name}_bucket{_format_labels(bucket_labels)} {cumulative}")
        # +Inf bucket
        inf_labels = dict(labels) if labels else {}
        inf_labels["le"] = "+Inf"
        lines.append(f"{name}_bucket{_format_labels(inf_labels)} {data['count']}")
        # Sum et count
        lines.append(f"{name}_sum{label_str} {data['sum']:.6f}")
        lines.append(f"{name}_count{label_str} {data['count']}")
    return "\n".join(lines) + "\n"


def generate_metrics_text() -> str:
    """Genere toutes les metriques au format Prometheus text exposition."""
    p = METRICS_PREFIX
    sections = []

    # ── HTTP Metrics ──
    sections.append(_render_counter(
        f"{p}_http_requests_total",
        "Total des requetes HTTP recues",
        request_count,
    ))
    sections.append(_render_histogram(
        f"{p}_http_request_duration_seconds",
        "Duree des requetes HTTP en secondes",
        request_latency,
    ))
    sections.append(_render_counter(
        f"{p}_http_errors_total",
        "Total des erreurs HTTP (4xx/5xx)",
        error_count,
    ))
    sections.append(_render_gauge(
        f"{p}_active_connections",
        "Nombre de connexions actives (WebSocket + HTTP)",
        active_connections,
    ))

    # ── Chain RPC Metrics ──
    sections.append(_render_histogram(
        f"{p}_chain_rpc_latency_seconds",
        "Latence des appels RPC blockchain en secondes",
        chain_rpc_latency,
    ))
    sections.append(_render_counter(
        f"{p}_chain_rpc_errors_total",
        "Total des erreurs RPC blockchain",
        chain_rpc_errors,
    ))
    sections.append(_render_gauge(
        f"{p}_chain_status",
        "Statut de la chain (1=up, 0=down)",
        chain_status,
    ))

    # ── Service Metrics ──
    sections.append(_render_histogram(
        f"{p}_service_execution_seconds",
        "Duree d'execution des services en secondes",
        service_execution_time,
    ))
    sections.append(_render_counter(
        f"{p}_service_success_total",
        "Total des executions de services reussies",
        service_success_count,
    ))
    sections.append(_render_counter(
        f"{p}_service_failure_total",
        "Total des executions de services echouees",
        service_failure_count,
    ))

    # ── Process info ──
    uptime = time.time() - _startup_time
    sections.append(f"# HELP {p}_uptime_seconds Uptime du processus en secondes\n"
                    f"# TYPE {p}_uptime_seconds gauge\n"
                    f"{p}_uptime_seconds {uptime:.1f}\n")

    return "\n".join(sections)


# ── SLA Dashboard ──

def get_sla_status() -> dict:
    """Retourne le tableau de bord SLA avec uptime actuel vs objectif par tier."""
    uptime_seconds = time.time() - _startup_time

    # Calculer l'uptime global
    total_checks = sum(c["total"] for c in _uptime_checks.values())
    total_up = sum(c["up"] for c in _uptime_checks.values())
    global_uptime_pct = (total_up / total_checks * 100) if total_checks > 0 else 100.0

    # Uptime par chain
    chain_uptimes = {}
    for chain in SUPPORTED_CHAINS:
        checks = _uptime_checks.get(chain, {"total": 0, "up": 0})
        if checks["total"] > 0:
            pct = (checks["up"] / checks["total"]) * 100
        else:
            pct = 100.0  # Pas de donnees = presume OK
        chain_uptimes[chain] = {
            "uptime_pct": round(pct, 3),
            "total_checks": checks["total"],
            "successful_checks": checks["up"],
            "status": "up" if chain_status.get(labels={"chain": chain}) >= 1 else (
                "down" if checks["total"] > 0 else "unknown"
            ),
        }

    # SLA par tier
    sla_compliance = {}
    for tier_name, target in SLA_TARGETS.items():
        target_pct = target["uptime_target"]
        compliant = global_uptime_pct >= target_pct
        margin = round(global_uptime_pct - target_pct, 3)
        sla_compliance[tier_name] = {
            "target_uptime_pct": target_pct,
            "current_uptime_pct": round(global_uptime_pct, 3),
            "compliant": compliant,
            "margin_pct": margin,
            "target_latency_ms": target["latency_target_ms"],
            "description": target["description"],
        }

    # Aggreger les stats des services
    service_stats = {}
    for labels, value in service_success_count.items():
        svc = labels.get("service", "unknown")
        if svc not in service_stats:
            service_stats[svc] = {"success": 0, "failure": 0, "success_rate": 0}
        service_stats[svc]["success"] = int(value)
    for labels, value in service_failure_count.items():
        svc = labels.get("service", "unknown")
        if svc not in service_stats:
            service_stats[svc] = {"success": 0, "failure": 0, "success_rate": 0}
        service_stats[svc]["failure"] = int(value)
    for svc, stats in service_stats.items():
        total = stats["success"] + stats["failure"]
        stats["success_rate"] = round((stats["success"] / total * 100), 2) if total > 0 else 0

    return {
        "uptime_seconds": round(uptime_seconds, 1),
        "global_uptime_pct": round(global_uptime_pct, 3),
        "sla_compliance": sla_compliance,
        "chain_uptimes": chain_uptimes,
        "service_stats": service_stats,
        "total_requests": sum(v for _, v in request_count.items()),
        "total_errors": sum(v for _, v in error_count.items()),
    }


# ── Middleware FastAPI ──

async def metrics_middleware(request: Request, call_next):
    """Middleware qui collecte automatiquement les metriques HTTP.

    A ajouter via app.middleware("http")(metrics_middleware) dans main.py.
    """
    if not METRICS_ENABLED:
        return await call_next(request)

    active_connections.inc()
    start = time.time()
    response = None
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        error_count.inc(labels={"method": request.method, "status": "500"})
        raise
    finally:
        duration = time.time() - start
        status_code = response.status_code if response else 500
        record_request(request.method, request.url.path, status_code, duration)
        active_connections.dec()


# ── Endpoints FastAPI ──

@router.get("/metrics", response_class=PlainTextResponse)
async def api_prometheus_metrics():
    """Endpoint Prometheus-compatible. Retourne les metriques au format text exposition.

    Configurer Prometheus scrape :
      - job_name: 'maxia'
        metrics_path: '/metrics'
        static_configs:
          - targets: ['your-host:8001']
    """
    if not METRICS_ENABLED:
        return PlainTextResponse(
            f"# MAXIA metrics disabled\n# Set METRICS_ENABLED=true\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    text = generate_metrics_text()
    return PlainTextResponse(
        text,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/api/enterprise/sla/status")
async def api_sla_status():
    """Tableau de bord SLA en JSON. Montre l'uptime actuel vs objectifs par tier."""
    return get_sla_status()


@router.get("/api/enterprise/metrics/summary")
async def api_metrics_summary():
    """Resume des metriques principales en JSON (alternative au format Prometheus)."""
    uptime = time.time() - _startup_time

    # Compter les requetes totales
    total_requests = sum(v for _, v in request_count.items())
    total_errors = sum(v for _, v in error_count.items())
    error_rate = (total_errors / total_requests * 100) if total_requests > 0 else 0

    # Latence moyenne des requetes
    latency_items = request_latency.items()
    total_latency_sum = sum(d["sum"] for _, d in latency_items)
    total_latency_count = sum(d["count"] for _, d in latency_items)
    avg_latency = (total_latency_sum / total_latency_count) if total_latency_count > 0 else 0

    return {
        "uptime_seconds": round(uptime, 1),
        "total_requests": int(total_requests),
        "total_errors": int(total_errors),
        "error_rate_pct": round(error_rate, 2),
        "avg_latency_seconds": round(avg_latency, 4),
        "active_connections": int(active_connections.get()),
        "chains_monitored": len([c for c in SUPPORTED_CHAINS if _uptime_checks.get(c, {}).get("total", 0) > 0]),
        "metrics_enabled": METRICS_ENABLED,
    }
