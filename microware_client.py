"""Microware Domain API client.

Single responsibility: given a config and a domain name, call
/domains/register (which IS the backorder when the target is in
pre-deletion parking). HTTP Basic auth, password from env.

Real money: the production endpoint charges 2604 Ft per successful
catch. Always go through `register_backorder` so the dry_run gate is
respected.
"""
from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests
import urllib3.util.connection as _urllib3_conn


@contextmanager
def _force_ipv4():
    """Pin outbound connections to IPv4 for the duration of the block.

    api.microware.hu publishes AAAA records, so this server reaches it over
    IPv6 by default - but microware only whitelists our IPv4 address, so IPv6
    connections are rejected pre-auth with a bare HTTP 404 (confirmed
    2026-06-02: forced IPv4 -> 401 auth challenge, forced IPv6 -> 404). The
    server's IPv6 is also volatile (SLAAC, ~1h prefix lifetime), so IPv4 is
    the stable, already-whitelisted, ip_guard-monitored path. The backorder
    submit is serialized, so the temporary swap of urllib3's resolver family
    is safe.
    """
    original = _urllib3_conn.allowed_gai_family
    _urllib3_conn.allowed_gai_family = lambda: socket.AF_INET
    try:
        yield
    finally:
        _urllib3_conn.allowed_gai_family = original

# Verbatim from microware API docs page 25. Must NOT be reformatted - the
# registry's HU+EN declaration text is matched literally on the registrar
# side.
HU_DECLARATION_TEXT = (
    "Nyilatkozom, hogy ezen domain igénylés kapcsán én vagyok az igénylő vagy "
    "jogosult vagyok az igénylő képviseletében eljárni. Szavatolom, hogy domain "
    "igénylésemben az adatokat a valóságnak megfelelően adtam meg, és tudomásul "
    "veszem, hogy amennyiben a megadott adatok nem valósak vagy az adatok "
    "megváltozását nem jelentem be, az a domain név visszavonását eredményezi. "
    "Megértettem, hogy a faktor adatok feletti rendelkezés megőrzésére különös "
    "figyelmet kell fordítanom. "
    "A [Domainregisztrációs Szabályzatot](https://www.domain.hu/domainregisztracios-szabalyzat/) "
    "megismertem, elfogadom és a mindenkor hatályos Domainregisztrációs "
    "Szabályzat előírásait a domain igénylés és fenntartás teljes tartama alatt "
    "betartom, és magamra vagy az általam képviselt domain igénylőre nézve "
    "kötelezőnek ismerem el. "
    "Kijelentem, hogy az igényléssel és a domain fenntartásával alávetem magam "
    "az [Alternatív Vitarendező Fórum](https://www.domain.hu/panaszkezeles/) "
    "döntéseinek. "
    "Megismertem az [Adatvédelmi Tájékoztatóban](https://www.domain.hu/adatkezeles/) "
    "foglaltakat, és személyes adataimnak az abban foglaltak szerinti kezelését "
    "elfogadom. "
    "Az összes adatot ellenőriztem és helyesek az adatok."
)


@dataclass
class RegisterResult:
    success: bool
    mode: str  # "live" | "dry_run"
    http_status: int | None = None
    api_code: int | None = None
    api_message: str = ""
    error_number: int | None = None
    order_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    request_body: dict[str, Any] = field(default_factory=dict)


def build_register_body(domain: str, cfg: dict) -> dict[str, Any]:
    """Construct the /domains/register POST body for a .hu backorder.

    microware drop-catches a still-parked (revoked) domain only when the
    domain field carries a `.backorder` suffix; a plain name is treated as an
    immediate registration and rejected with errorno 10256 while the target is
    still parked. Backorders must also request a single year (years>1 ->
    errorno 10255). Both confirmed by microware support, ticket #497545."""
    mw = cfg["microware"]
    target = domain if domain.endswith(".backorder") else f"{domain}.backorder"
    return {
        "domain": target,
        "years": mw["registration_years"],
        "ns1": mw["ns1"],
        "ns2": mw["ns2"],
        "owner": mw["owner_contact_id"],
        "type": mw["domain_type"],
        "declarations": HU_DECLARATION_TEXT,
    }


def register_backorder(
    domain: str, cfg: dict, *, dry_run: bool, log_path: str | None = None
) -> RegisterResult:
    """Submit a backorder. When dry_run, return synthetic success without
    POSTing. Real call uses HTTP Basic with MICROWARE_API_PASSWORD env."""
    body = build_register_body(domain, cfg)

    if dry_run:
        if log_path:
            line = json.dumps(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "domain": domain,
                    "body": body,
                },
                ensure_ascii=False,
            )
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return RegisterResult(success=True, mode="dry_run", request_body=body)

    password = os.environ.get("MICROWARE_API_PASSWORD")
    if not password:
        return RegisterResult(
            success=False,
            mode="live",
            api_message="MICROWARE_API_PASSWORD env not set",
            request_body=body,
        )

    url = cfg["microware"]["base_url"].rstrip("/") + "/domains/register"
    with _force_ipv4():
        resp = requests.post(
            url,
            data=body,
            auth=(cfg["microware"]["username"], password),
            timeout=30,
        )
    return _parse_response(resp, body)


def _parse_response(resp: requests.Response, body: dict[str, Any]) -> RegisterResult:
    try:
        payload = resp.json()
    except ValueError:
        return RegisterResult(
            success=False,
            mode="live",
            http_status=resp.status_code,
            api_message=f"non-JSON response: {resp.text[:200]}",
            request_body=body,
        )

    result = payload.get("result", {})
    api_code = result.get("code")
    api_message = result.get("message", "")
    order_id = payload.get("domain", {}).get("orderid")
    error_number: int | None = None
    if api_code != 201:
        for tok in api_message.replace(":", " ").split():
            if tok.isdigit() and len(tok) == 5:
                error_number = int(tok)
                break

    return RegisterResult(
        success=api_code == 201,
        mode="live",
        http_status=resp.status_code,
        api_code=api_code,
        api_message=api_message,
        error_number=error_number,
        order_id=order_id,
        raw=payload,
        request_body=body,
    )
