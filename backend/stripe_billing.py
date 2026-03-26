"""MAXIA Stripe Billing V12 — Integration Stripe reelle pour abonnements enterprise.

Gere le cycle de vie complet des abonnements Stripe :
- Creation de sessions Checkout pour chaque tier (Pro, Enterprise, Fleet, Compliance)
- Reception et traitement des webhooks Stripe (paiement, annulation, renouvellement)
- Lien entre stripe_customer_id et tenant_id MAXIA en DB
- Portail client Stripe pour gerer l'abonnement
- Degradation gracieuse si STRIPE_SECRET_KEY non configure

Variables d'environnement requises :
  STRIPE_SECRET_KEY       — Cle secrete Stripe (sk_live_... ou sk_test_...)
  STRIPE_WEBHOOK_SECRET   — Secret de verification webhook (whsec_...)
  STRIPE_PRICE_PRO        — Price ID Stripe pour le plan Pro ($9.99/mois)
  STRIPE_PRICE_ENTERPRISE — Price ID Stripe pour le plan Enterprise ($299/mois)
  STRIPE_PRICE_FLEET      — Price ID Stripe pour le plan Fleet (custom)
  STRIPE_PRICE_COMPLIANCE — Price ID Stripe pour le plan Compliance (custom)
"""

import os
import time
import json
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

# ── Config Stripe via env vars ──

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "")
STRIPE_PRICE_ENTERPRISE = os.getenv("STRIPE_PRICE_ENTERPRISE", "")
STRIPE_PRICE_FLEET = os.getenv("STRIPE_PRICE_FLEET", "")
STRIPE_PRICE_COMPLIANCE = os.getenv("STRIPE_PRICE_COMPLIANCE", "")

# URL de retour apres checkout (configurable, defaut landing MAXIA)
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://maxiaworld.app/app.html?stripe=success")
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "https://maxiaworld.app/app.html?stripe=cancel")

# Mapping plan -> Price ID Stripe
PLAN_PRICE_MAP = {
    "pro": STRIPE_PRICE_PRO,
    "enterprise": STRIPE_PRICE_ENTERPRISE,
    "fleet": STRIPE_PRICE_FLEET,
    "compliance": STRIPE_PRICE_COMPLIANCE,
}

# Mapping plan -> tier enterprise_billing pour synchronisation
PLAN_TIER_MAP = {
    "pro": "pro",
    "enterprise": "enterprise",
    "fleet": "custom",
    "compliance": "custom",
}

# ── Import conditionnel du SDK Stripe ──

stripe = None
STRIPE_AVAILABLE = False

if STRIPE_SECRET_KEY:
    try:
        import stripe as _stripe
        _stripe.api_key = STRIPE_SECRET_KEY
        # Fixer la version API pour stabilite en production
        _stripe.api_version = "2024-12-18.acacia"
        stripe = _stripe
        STRIPE_AVAILABLE = True
        print("[Stripe] SDK initialise avec succes")
    except ImportError:
        print("[Stripe] ERREUR: package 'stripe' non installe. Lancer: pip install stripe")
    except Exception as e:
        print(f"[Stripe] ERREUR init: {e}")
else:
    print("[Stripe] STRIPE_SECRET_KEY non configure — module desactive")

# ── Schema DB ──

_schema_ready = False

_STRIPE_SCHEMA = """
CREATE TABLE IF NOT EXISTS stripe_subscriptions (
    tenant_id TEXT NOT NULL,
    stripe_customer_id TEXT NOT NULL,
    stripe_subscription_id TEXT PRIMARY KEY,
    plan TEXT NOT NULL DEFAULT 'pro',
    status TEXT NOT NULL DEFAULT 'incomplete',
    current_period_end INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_stripe_tenant ON stripe_subscriptions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_stripe_customer ON stripe_subscriptions(stripe_customer_id);

CREATE TABLE IF NOT EXISTS stripe_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    processed_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    data TEXT NOT NULL DEFAULT '{}'
);
"""


async def _ensure_schema():
    """Cree les tables Stripe si elles n'existent pas encore."""
    global _schema_ready
    if _schema_ready:
        return
    try:
        from database import db
        await db.raw_executescript(_STRIPE_SCHEMA)
        _schema_ready = True
        print("[Stripe] Schema DB pret")
    except Exception as e:
        print(f"[Stripe] Erreur schema: {e}")


# ── Fonctions utilitaires DB ──

async def _get_subscription_by_tenant(tenant_id: str) -> Optional[dict]:
    """Recupere l'abonnement Stripe actif d'un tenant."""
    from database import db
    await _ensure_schema()
    rows = await db.raw_execute_fetchall(
        "SELECT tenant_id, stripe_customer_id, stripe_subscription_id, plan, status, "
        "current_period_end FROM stripe_subscriptions "
        "WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 1",
        (tenant_id,),
    )
    if not rows:
        return None
    row = rows[0]
    if isinstance(row, dict):
        return row
    return {
        "tenant_id": row[0],
        "stripe_customer_id": row[1],
        "stripe_subscription_id": row[2],
        "plan": row[3],
        "status": row[4],
        "current_period_end": row[5],
    }


async def _get_subscription_by_customer(stripe_customer_id: str) -> Optional[dict]:
    """Recupere l'abonnement par customer_id Stripe."""
    from database import db
    await _ensure_schema()
    rows = await db.raw_execute_fetchall(
        "SELECT tenant_id, stripe_customer_id, stripe_subscription_id, plan, status, "
        "current_period_end FROM stripe_subscriptions "
        "WHERE stripe_customer_id = ? ORDER BY created_at DESC LIMIT 1",
        (stripe_customer_id,),
    )
    if not rows:
        return None
    row = rows[0]
    if isinstance(row, dict):
        return row
    return {
        "tenant_id": row[0],
        "stripe_customer_id": row[1],
        "stripe_subscription_id": row[2],
        "plan": row[3],
        "status": row[4],
        "current_period_end": row[5],
    }


async def _get_subscription_by_stripe_sub_id(stripe_subscription_id: str) -> Optional[dict]:
    """Recupere l'abonnement par subscription_id Stripe."""
    from database import db
    await _ensure_schema()
    rows = await db.raw_execute_fetchall(
        "SELECT tenant_id, stripe_customer_id, stripe_subscription_id, plan, status, "
        "current_period_end FROM stripe_subscriptions "
        "WHERE stripe_subscription_id = ?",
        (stripe_subscription_id,),
    )
    if not rows:
        return None
    row = rows[0]
    if isinstance(row, dict):
        return row
    return {
        "tenant_id": row[0],
        "stripe_customer_id": row[1],
        "stripe_subscription_id": row[2],
        "plan": row[3],
        "status": row[4],
        "current_period_end": row[5],
    }


async def _upsert_subscription(
    tenant_id: str,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    plan: str,
    status: str,
    current_period_end: int,
):
    """Insere ou met a jour un abonnement Stripe en DB."""
    from database import db
    await _ensure_schema()
    now_ts = int(time.time())

    existing = await _get_subscription_by_stripe_sub_id(stripe_subscription_id)
    if existing:
        await db.raw_execute(
            "UPDATE stripe_subscriptions SET status = ?, plan = ?, "
            "current_period_end = ?, updated_at = ? "
            "WHERE stripe_subscription_id = ?",
            (status, plan, current_period_end, now_ts, stripe_subscription_id),
        )
    else:
        await db.raw_execute(
            "INSERT INTO stripe_subscriptions "
            "(tenant_id, stripe_customer_id, stripe_subscription_id, plan, status, "
            "current_period_end, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, stripe_customer_id, stripe_subscription_id,
             plan, status, current_period_end, now_ts, now_ts),
        )
    print(f"[Stripe] Subscription {stripe_subscription_id} -> tenant={tenant_id} status={status}")


async def _record_event(event_id: str, event_type: str, data: dict):
    """Enregistre un evenement Stripe pour idempotence et audit."""
    from database import db
    await _ensure_schema()
    try:
        await db.raw_execute(
            "INSERT OR IGNORE INTO stripe_events (event_id, event_type, data, processed_at) "
            "VALUES (?, ?, ?, ?)",
            (event_id, event_type, json.dumps(data), int(time.time())),
        )
    except Exception as e:
        # Pas critique — l'event est deja traite si doublon
        print(f"[Stripe] Event record warning: {e}")


async def _event_already_processed(event_id: str) -> bool:
    """Verifie si un evenement Stripe a deja ete traite (idempotence)."""
    from database import db
    await _ensure_schema()
    rows = await db.raw_execute_fetchall(
        "SELECT event_id FROM stripe_events WHERE event_id = ?",
        (event_id,),
    )
    return len(rows) > 0


# ── Synchronisation avec enterprise_billing ──

async def _sync_billing_tier(tenant_id: str, plan: str, active: bool):
    """Synchronise le tier dans enterprise_billing quand le statut Stripe change."""
    try:
        from database import db
        tier = PLAN_TIER_MAP.get(plan, "free") if active else "free"
        # Verifier si le tenant existe dans billing_tenants
        rows = await db.raw_execute_fetchall(
            "SELECT tenant_id FROM billing_tenants WHERE tenant_id = ?",
            (tenant_id,),
        )
        if rows:
            await db.raw_execute(
                "UPDATE billing_tenants SET tier = ? WHERE tenant_id = ?",
                (tier, tenant_id),
            )
        else:
            await db.raw_execute(
                "INSERT INTO billing_tenants (tenant_id, tier) VALUES (?, ?)",
                (tenant_id, tier),
            )
        print(f"[Stripe] Tier billing synchronise: tenant={tenant_id} tier={tier}")
    except Exception as e:
        # La table billing_tenants peut ne pas exister si enterprise_billing n'est pas charge
        print(f"[Stripe] Sync billing tier warning: {e}")


# ── Logique metier Stripe ──

def _resolve_plan_from_price_id(price_id: str) -> str:
    """Determine le plan MAXIA a partir du Price ID Stripe."""
    for plan_name, pid in PLAN_PRICE_MAP.items():
        if pid and pid == price_id:
            return plan_name
    return "pro"  # Defaut si Price ID inconnu


async def create_checkout_session(tenant_id: str, plan: str, email: Optional[str] = None) -> dict:
    """Cree une session Stripe Checkout pour un plan donne.

    Retourne l'URL de checkout ou le tenant doit etre redirige.
    """
    if not STRIPE_AVAILABLE:
        raise HTTPException(503, "Stripe non configure. Contactez support@maxiaworld.app")

    price_id = PLAN_PRICE_MAP.get(plan)
    if not price_id:
        raise HTTPException(
            400,
            f"Plan invalide ou Price ID non configure: {plan}. "
            f"Plans disponibles: {', '.join(p for p, pid in PLAN_PRICE_MAP.items() if pid)}",
        )

    # Chercher un customer existant pour ce tenant
    existing_sub = await _get_subscription_by_tenant(tenant_id)
    customer_id = existing_sub["stripe_customer_id"] if existing_sub else None

    # Parametres de la session Checkout
    session_params = {
        "mode": "subscription",
        "payment_method_types": ["card"],
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": f"{STRIPE_SUCCESS_URL}&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": STRIPE_CANCEL_URL,
        "metadata": {
            "tenant_id": tenant_id,
            "plan": plan,
        },
        "subscription_data": {
            "metadata": {
                "tenant_id": tenant_id,
                "plan": plan,
            },
        },
    }

    # Rattacher au customer existant ou pre-remplir l'email
    if customer_id:
        session_params["customer"] = customer_id
    elif email:
        session_params["customer_email"] = email

    # Autoriser les codes promo
    session_params["allow_promotion_codes"] = True

    try:
        session = stripe.checkout.Session.create(**session_params)
        print(f"[Stripe] Checkout session creee: {session.id} pour tenant={tenant_id} plan={plan}")
        return {
            "checkout_url": session.url,
            "session_id": session.id,
            "plan": plan,
            "tenant_id": tenant_id,
        }
    except stripe.error.StripeError as e:
        print(f"[Stripe] Erreur Checkout: {e}")
        raise HTTPException(502, f"Erreur Stripe: {e.user_message or str(e)}")


async def create_portal_session(tenant_id: str) -> dict:
    """Cree une session Stripe Customer Portal pour gerer l'abonnement.

    Permet au client de modifier sa carte, changer de plan, annuler, etc.
    """
    if not STRIPE_AVAILABLE:
        raise HTTPException(503, "Stripe non configure")

    sub = await _get_subscription_by_tenant(tenant_id)
    if not sub or not sub.get("stripe_customer_id"):
        raise HTTPException(404, f"Aucun abonnement Stripe trouve pour le tenant {tenant_id}")

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=sub["stripe_customer_id"],
            return_url=f"{STRIPE_SUCCESS_URL.split('?')[0]}?portal=return",
        )
        return {
            "portal_url": portal_session.url,
            "tenant_id": tenant_id,
        }
    except stripe.error.StripeError as e:
        print(f"[Stripe] Erreur Portal: {e}")
        raise HTTPException(502, f"Erreur Stripe portal: {e.user_message or str(e)}")


async def get_subscription_status(tenant_id: str) -> dict:
    """Retourne le statut de l'abonnement Stripe d'un tenant."""
    sub = await _get_subscription_by_tenant(tenant_id)
    if not sub:
        return {
            "tenant_id": tenant_id,
            "has_subscription": False,
            "status": "none",
            "plan": "free",
            "message": "Aucun abonnement Stripe actif",
        }

    # Verifier si l'abonnement est encore valide temporellement
    is_active = sub["status"] in ("active", "trialing")
    is_period_valid = sub["current_period_end"] > int(time.time())

    # Si Stripe est disponible, on peut verifier en live
    live_status = None
    if STRIPE_AVAILABLE and sub.get("stripe_subscription_id"):
        try:
            live_sub = stripe.Subscription.retrieve(sub["stripe_subscription_id"])
            live_status = live_sub.status
            # Mettre a jour la DB si le statut a change
            if live_status != sub["status"] or live_sub.current_period_end != sub["current_period_end"]:
                await _upsert_subscription(
                    tenant_id=sub["tenant_id"],
                    stripe_customer_id=sub["stripe_customer_id"],
                    stripe_subscription_id=sub["stripe_subscription_id"],
                    plan=sub["plan"],
                    status=live_status,
                    current_period_end=live_sub.current_period_end,
                )
                is_active = live_status in ("active", "trialing")
                is_period_valid = live_sub.current_period_end > int(time.time())
        except Exception as e:
            print(f"[Stripe] Erreur verification live: {e}")
            # Pas grave — on utilise les donnees DB

    return {
        "tenant_id": tenant_id,
        "has_subscription": True,
        "status": live_status or sub["status"],
        "plan": sub["plan"],
        "is_active": is_active and is_period_valid,
        "current_period_end": sub["current_period_end"],
        "current_period_end_iso": datetime.fromtimestamp(
            sub["current_period_end"], tz=timezone.utc
        ).isoformat() if sub["current_period_end"] > 0 else None,
        "stripe_customer_id": sub["stripe_customer_id"],
    }


# ── Fonction publique pour enterprise_billing ──

async def has_active_stripe_subscription(tenant_id: str) -> bool:
    """Verifie si un tenant a un abonnement Stripe actif.

    Utilisee par enterprise_billing.generate_invoice() pour savoir
    si le prix de base est deja couvert par Stripe.
    """
    sub = await _get_subscription_by_tenant(tenant_id)
    if not sub:
        return False
    is_active = sub["status"] in ("active", "trialing")
    is_period_valid = sub["current_period_end"] > int(time.time())
    return is_active and is_period_valid


# ── Webhook Handler ──

def _verify_webhook_signature(payload: bytes, sig_header: str) -> dict:
    """Verifie la signature du webhook Stripe et retourne l'evenement.

    Utilise le SDK Stripe pour la verification cryptographique.
    Leve une exception si la signature est invalide.
    """
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(500, "STRIPE_WEBHOOK_SECRET non configure")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
        return event
    except stripe.error.SignatureVerificationError as e:
        print(f"[Stripe] Signature webhook invalide: {e}")
        raise HTTPException(400, "Signature webhook invalide")
    except ValueError as e:
        print(f"[Stripe] Payload webhook invalide: {e}")
        raise HTTPException(400, "Payload webhook invalide")


async def handle_webhook_event(event: dict) -> dict:
    """Traite un evenement webhook Stripe verifie.

    Evenements geres :
    - checkout.session.completed  : Nouveau client, creer l'abonnement en DB
    - invoice.paid                : Renouvellement reussi, mettre a jour la periode
    - customer.subscription.updated : Changement de plan ou statut
    - customer.subscription.deleted : Annulation, passer le tenant en free
    - invoice.payment_failed      : Echec de paiement, alerter
    """
    event_type = event.get("type", "")
    event_id = event.get("id", "")
    data_object = event.get("data", {}).get("object", {})

    # Idempotence — ne pas traiter un evenement deux fois
    if await _event_already_processed(event_id):
        return {"status": "already_processed", "event_id": event_id}

    result = {"status": "ignored", "event_type": event_type}

    if event_type == "checkout.session.completed":
        result = await _handle_checkout_completed(data_object)

    elif event_type == "invoice.paid":
        result = await _handle_invoice_paid(data_object)

    elif event_type == "customer.subscription.updated":
        result = await _handle_subscription_updated(data_object)

    elif event_type == "customer.subscription.deleted":
        result = await _handle_subscription_deleted(data_object)

    elif event_type == "invoice.payment_failed":
        result = await _handle_payment_failed(data_object)

    # Enregistrer l'evenement comme traite
    await _record_event(event_id, event_type, {
        "result": result.get("status", "processed"),
        "tenant_id": result.get("tenant_id", ""),
    })

    return result


async def _handle_checkout_completed(session: dict) -> dict:
    """Traite la fin d'un checkout Stripe — lie le customer au tenant."""
    metadata = session.get("metadata", {})
    tenant_id = metadata.get("tenant_id", "")
    plan = metadata.get("plan", "pro")
    customer_id = session.get("customer", "")
    subscription_id = session.get("subscription", "")

    if not tenant_id or not customer_id:
        print(f"[Stripe] checkout.session.completed sans tenant_id ou customer_id")
        return {"status": "error", "reason": "missing_metadata"}

    # Recuperer les details de la subscription depuis Stripe
    current_period_end = 0
    status = "active"
    if STRIPE_AVAILABLE and subscription_id:
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            current_period_end = sub.current_period_end
            status = sub.status
            # Recuperer le plan depuis les items si pas dans metadata
            if sub.get("items") and sub["items"].get("data"):
                price_id = sub["items"]["data"][0].get("price", {}).get("id", "")
                if price_id:
                    plan = _resolve_plan_from_price_id(price_id)
        except Exception as e:
            print(f"[Stripe] Erreur recuperation subscription: {e}")

    # Sauvegarder en DB
    await _upsert_subscription(
        tenant_id=tenant_id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        plan=plan,
        status=status,
        current_period_end=current_period_end,
    )

    # Synchroniser le tier dans enterprise_billing
    await _sync_billing_tier(tenant_id, plan, active=True)

    print(f"[Stripe] Checkout complete: tenant={tenant_id} plan={plan} customer={customer_id}")
    return {
        "status": "subscription_created",
        "tenant_id": tenant_id,
        "plan": plan,
        "stripe_customer_id": customer_id,
    }


async def _handle_invoice_paid(invoice: dict) -> dict:
    """Traite un paiement de facture reussi — renouvellement mensuel."""
    customer_id = invoice.get("customer", "")
    subscription_id = invoice.get("subscription", "")

    if not customer_id or not subscription_id:
        return {"status": "ignored", "reason": "no_subscription"}

    # Chercher le tenant associe
    sub = await _get_subscription_by_stripe_sub_id(subscription_id)
    if not sub:
        # Tenter de trouver par customer_id
        sub = await _get_subscription_by_customer(customer_id)

    if not sub:
        print(f"[Stripe] invoice.paid pour subscription inconnue: {subscription_id}")
        return {"status": "ignored", "reason": "unknown_subscription"}

    # Mettre a jour la periode via l'API Stripe
    current_period_end = sub["current_period_end"]
    if STRIPE_AVAILABLE and subscription_id:
        try:
            live_sub = stripe.Subscription.retrieve(subscription_id)
            current_period_end = live_sub.current_period_end
        except Exception as e:
            print(f"[Stripe] Erreur retrieve subscription: {e}")

    await _upsert_subscription(
        tenant_id=sub["tenant_id"],
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        plan=sub["plan"],
        status="active",
        current_period_end=current_period_end,
    )

    print(f"[Stripe] Invoice paid: tenant={sub['tenant_id']} renouvele jusqu'a {current_period_end}")
    return {
        "status": "subscription_renewed",
        "tenant_id": sub["tenant_id"],
        "current_period_end": current_period_end,
    }


async def _handle_subscription_updated(subscription: dict) -> dict:
    """Traite une mise a jour d'abonnement (changement de plan, pause, etc.)."""
    subscription_id = subscription.get("id", "")
    customer_id = subscription.get("customer", "")
    status = subscription.get("status", "active")
    current_period_end = subscription.get("current_period_end", 0)

    sub = await _get_subscription_by_stripe_sub_id(subscription_id)
    if not sub:
        sub = await _get_subscription_by_customer(customer_id)

    if not sub:
        print(f"[Stripe] subscription.updated pour subscription inconnue: {subscription_id}")
        return {"status": "ignored", "reason": "unknown_subscription"}

    # Determiner le plan depuis les items
    plan = sub["plan"]
    items = subscription.get("items", {}).get("data", [])
    if items:
        price_id = items[0].get("price", {}).get("id", "")
        if price_id:
            plan = _resolve_plan_from_price_id(price_id)

    await _upsert_subscription(
        tenant_id=sub["tenant_id"],
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        plan=plan,
        status=status,
        current_period_end=current_period_end,
    )

    # Synchroniser le tier billing
    is_active = status in ("active", "trialing")
    await _sync_billing_tier(sub["tenant_id"], plan, active=is_active)

    print(f"[Stripe] Subscription updated: tenant={sub['tenant_id']} plan={plan} status={status}")
    return {
        "status": "subscription_updated",
        "tenant_id": sub["tenant_id"],
        "plan": plan,
        "stripe_status": status,
    }


async def _handle_subscription_deleted(subscription: dict) -> dict:
    """Traite l'annulation d'un abonnement — repasser le tenant en free."""
    subscription_id = subscription.get("id", "")
    customer_id = subscription.get("customer", "")

    sub = await _get_subscription_by_stripe_sub_id(subscription_id)
    if not sub:
        sub = await _get_subscription_by_customer(customer_id)

    if not sub:
        print(f"[Stripe] subscription.deleted pour subscription inconnue: {subscription_id}")
        return {"status": "ignored", "reason": "unknown_subscription"}

    await _upsert_subscription(
        tenant_id=sub["tenant_id"],
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        plan=sub["plan"],
        status="canceled",
        current_period_end=sub["current_period_end"],
    )

    # Repasser en free dans enterprise_billing
    await _sync_billing_tier(sub["tenant_id"], "free", active=False)

    print(f"[Stripe] Subscription annulee: tenant={sub['tenant_id']}")
    return {
        "status": "subscription_canceled",
        "tenant_id": sub["tenant_id"],
    }


async def _handle_payment_failed(invoice: dict) -> dict:
    """Traite un echec de paiement — alerte sans annulation immediate.

    Stripe reessaiera automatiquement selon la politique de retry configuree.
    On log l'echec pour monitoring.
    """
    customer_id = invoice.get("customer", "")
    subscription_id = invoice.get("subscription", "")
    attempt_count = invoice.get("attempt_count", 0)

    sub = await _get_subscription_by_stripe_sub_id(subscription_id) if subscription_id else None
    if not sub and customer_id:
        sub = await _get_subscription_by_customer(customer_id)

    tenant_id = sub["tenant_id"] if sub else "unknown"

    print(f"[Stripe] ALERTE: Paiement echoue pour tenant={tenant_id} "
          f"(tentative #{attempt_count}, subscription={subscription_id})")

    # Envoyer une alerte Discord si le webhook est configure
    try:
        from alerts import send_alert
        await send_alert(
            f"Stripe paiement echoue — tenant={tenant_id} "
            f"tentative #{attempt_count}",
            level="warning",
        )
    except Exception:
        pass  # Pas de dependance dure sur les alertes

    return {
        "status": "payment_failed_logged",
        "tenant_id": tenant_id,
        "attempt_count": attempt_count,
    }


# ── Router FastAPI ──

router = APIRouter(prefix="/api/enterprise/stripe", tags=["stripe-billing"])


class CheckoutRequest(BaseModel):
    tenant_id: str
    plan: str
    email: Optional[str] = None


@router.post("/checkout")
async def api_create_checkout(req: CheckoutRequest):
    """Cree une session Stripe Checkout pour souscrire a un plan.

    Plans disponibles : pro, enterprise, fleet, compliance.
    Retourne l'URL vers laquelle rediriger le client.
    """
    if not STRIPE_AVAILABLE:
        return {
            "status": "stripe_not_configured",
            "message": "Stripe non configure. Definir STRIPE_SECRET_KEY dans .env",
        }

    if not req.tenant_id or len(req.tenant_id) > 128:
        raise HTTPException(400, "tenant_id invalide (1-128 caracteres)")

    valid_plans = [p for p, pid in PLAN_PRICE_MAP.items() if pid]
    if req.plan not in valid_plans:
        raise HTTPException(400, f"Plan invalide: {req.plan}. Plans configures: {', '.join(valid_plans)}")

    return await create_checkout_session(req.tenant_id, req.plan, req.email)


@router.post("/webhook")
async def api_stripe_webhook(request: Request):
    """Recoit et traite les webhooks Stripe.

    Verifie la signature via STRIPE_WEBHOOK_SECRET avant traitement.
    Le body doit etre lu en raw bytes pour la verification.
    """
    if not STRIPE_AVAILABLE:
        raise HTTPException(503, "Stripe non configure")

    # Lire le body raw pour la verification de signature
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not sig_header:
        raise HTTPException(400, "Header stripe-signature manquant")

    # Verifier la signature et parser l'evenement
    event = _verify_webhook_signature(payload, sig_header)

    # Traiter l'evenement
    result = await handle_webhook_event(event)

    # Toujours retourner 200 a Stripe pour confirmer la reception
    return {"received": True, **result}


@router.get("/portal/{tenant_id}")
async def api_customer_portal(tenant_id: str):
    """Genere un lien vers le portail client Stripe.

    Permet au client de gerer son abonnement, sa carte, ses factures Stripe.
    """
    if not STRIPE_AVAILABLE:
        return {
            "status": "stripe_not_configured",
            "message": "Stripe non configure",
        }

    if not tenant_id:
        raise HTTPException(400, "tenant_id requis")

    return await create_portal_session(tenant_id)


@router.get("/status/{tenant_id}")
async def api_subscription_status(tenant_id: str):
    """Retourne le statut de l'abonnement Stripe d'un tenant.

    Inclut : plan actif, statut, date de fin de periode, verification live optionnelle.
    """
    if not STRIPE_AVAILABLE:
        return {
            "tenant_id": tenant_id,
            "status": "stripe_not_configured",
            "has_subscription": False,
            "plan": "free",
            "message": "Stripe non configure — facturation manuelle uniquement",
        }

    if not tenant_id:
        raise HTTPException(400, "tenant_id requis")

    return await get_subscription_status(tenant_id)
