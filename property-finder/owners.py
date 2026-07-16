"""Unmask the LLC: real owner-contact info from HPD registration, by BBL.

NYC requires residential building owners to register with HPD and name actual
people — a Head Officer / owner and a managing agent — with a business mailing
address.  That turns the anonymous single-purpose LLC on the deed into a person
you can write to or skip-trace.

  * Registrations  tesw-yqqr  (building -> registrationid, by boro/block/lot)
  * Contacts       feu5-w2e2  (registrationid -> named people + addresses)

This is legally-public business-registration info for income property; use it
for professional acquisition outreach.
"""
from collections import defaultdict

import config as C
import nyc_api as api

# Which contact is "the principal", best first.
PRINCIPAL_ORDER = ("HeadOfficer", "IndividualOwner", "Officer", "JointOwner",
                   "CorporateOwner", "Shareholder")


def _key_from_bbl(bbl):
    """10-digit BBL -> (boroid, block, lot) as tesw-yqqr stores them."""
    return (bbl[0], str(int(bbl[1:6])), str(int(bbl[6:10])))


def _name(c):
    nm = " ".join(x for x in (c.get("firstname"), c.get("lastname")) if x).strip()
    return nm or (c.get("corporationname") or "").strip()


def _addr(c):
    line1 = " ".join(str(x) for x in (c.get("businesshousenumber"),
                                      c.get("businessstreetname"),
                                      c.get("businessapartment")) if x)
    line2 = " ".join(str(x) for x in (c.get("businesscity"),
                                      c.get("businessstate"),
                                      c.get("businesszip")) if x)
    return ", ".join(x for x in (line1, line2) if x)


def _summarize(contacts):
    by_type = defaultdict(list)
    for c in contacts:
        by_type[c.get("type", "")].append(c)

    principal, ptype = None, ""
    for t in PRINCIPAL_ORDER:
        if by_type.get(t):
            principal, ptype = by_type[t][0], t
            break
    agent = (by_type.get("Agent") or [None])[0]
    corp = (by_type.get("CorporateOwner") or [None])[0]

    return {
        "owner_name": _name(principal) if principal else "",
        "owner_title": (principal.get("title") or ptype) if principal else "",
        "owner_address": _addr(principal) if principal else "",
        "owner_entity": _name(corp) if corp else "",
        "agent_name": _name(agent) if agent else "",
    }


def fetch_registration_ids(boroughs, use_cache=True, log=print):
    """(boroid, block, lot) -> most-recent registrationid.  Bulk-pulled."""
    codes = ",".join(f"'{C.BORO_ABBR_TO_CODE[b]}'" for b in boroughs)
    rows = api.soda_get_all(
        "tesw-yqqr", where=f"boroid in ({codes})",
        select="boroid,block,lot,registrationid,lastregistrationdate",
        use_cache=use_cache, log=log)
    best = {}
    for r in rows:
        try:
            key = (r["boroid"], str(int(r["block"])), str(int(r["lot"])))
        except (KeyError, ValueError):
            continue
        dt = r.get("lastregistrationdate", "") or ""
        if key not in best or dt > best[key][1]:
            best[key] = (r["registrationid"], dt)
    return {k: v[0] for k, v in best.items()}


def fetch_contacts(reg_ids, use_cache=True, log=print):
    """registrationid -> summarized owner/agent contact dict."""
    rows = api.fetch_in(
        "feu5-w2e2", "registrationid", reg_ids,
        select=("registrationid,type,firstname,lastname,corporationname,title,"
                "businesshousenumber,businessstreetname,businessapartment,"
                "businesscity,businessstate,businesszip"),
        use_cache=use_cache, log=log)
    by_reg = defaultdict(list)
    for r in rows:
        by_reg[r["registrationid"]].append(r)
    return {rid: _summarize(cs) for rid, cs in by_reg.items()}


def fetch_all(bbls, boroughs, use_cache=True, log=print):
    """bbl -> owner-contact dict (+ hpd_registration_id)."""
    log(f"  HPD registrations for {len(boroughs)} boroughs…")
    reg_by_key = fetch_registration_ids(boroughs, use_cache, log)
    reg_for_bbl = {}
    for bbl in bbls:
        rid = reg_by_key.get(_key_from_bbl(bbl))
        if rid:
            reg_for_bbl[bbl] = rid
    log(f"  {len(reg_for_bbl)}/{len(bbls)} parcels have an HPD registration; "
        f"fetching contacts…")
    contacts = fetch_contacts(set(reg_for_bbl.values()), use_cache, log)

    blank = {"owner_name": "", "owner_title": "", "owner_address": "",
             "owner_entity": "", "agent_name": ""}
    out = {}
    for bbl in bbls:
        rid = reg_for_bbl.get(bbl)
        info = dict(contacts.get(rid, blank)) if rid else dict(blank)
        info["hpd_registration_id"] = rid or ""
        out[bbl] = info
    named = sum(1 for v in out.values() if v["owner_name"])
    log(f"  named an owner/officer for {named} parcels")
    return out
