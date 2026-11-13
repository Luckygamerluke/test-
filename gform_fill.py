#!/usr/bin/env python3
"""Remplit un Google Form (public, multi-sections) à partir d'un CSV. 1 ligne = 1 réponse.

Étapes:
  1) python gform_fill.py URL --questions                 -> liste les questions du formulaire
  2) python gform_fill.py URL data.csv --show-mapping     -> montre quelle colonne va dans quelle question
  3) python gform_fill.py URL data.csv --dry-run          -> simule, n'envoie rien
  4) python gform_fill.py URL data.csv --limit 1          -> envoie 1 seule ligne (test réel)
  5) python gform_fill.py URL data.csv                    -> envoie tout

Si le mapping automatique se trompe: --save-mapping mapping.json, corrige le fichier,
puis relance avec --mapping mapping.json.

CSV: séparateur ; ou , détecté tout seul. Cases à cocher: choix séparés par |.
Colonnes ignorées: id.
"""
import argparse
import csv
import difflib
import json
import re
import sys
import time

import requests

TYPES = {0: "texte court", 1: "paragraphe", 2: "choix unique", 3: "liste déroulante",
         4: "cases à cocher", 5: "échelle", 9: "date", 10: "heure"}
CHOICE = (2, 3, 4, 5)
IGNORE_COLS = {"id"}
# Synonymes pour des valeurs CSV en anglais -> options du formulaire en français.
SYN = {"consumer": ["consomm", "particulier"], "distributor": ["distribut", "import", "grossiste"],
       "restaurant": ["restau", "café", "cafe", "hôtel", "hotel", "chr"]}


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def form_urls(url):
    m = re.search(r"/forms/d/e/([^/]+)", url)
    if not m:
        sys.exit("URL invalide: utilise le lien public .../forms/d/e/.../viewform")
    base = f"https://docs.google.com/forms/d/e/{m.group(1)}"
    return base + "/viewform", base + "/formResponse"


def parse_form(html):
    m = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(.*?);\s*</script>", html, re.S)
    if not m:
        sys.exit("Formulaire illisible (connexion Google requise ? lien incorrect ?).")
    data = json.loads(m.group(1))
    page, qs = 0, []
    for it in data[1][1] or []:
        if len(it) > 3 and it[3] == 8:  # saut de section
            page += 1
            continue
        if len(it) < 5 or not it[4]:
            continue
        for sub in it[4]:
            opts = [o[0] for o in sub[1] if o and o[0]] if len(sub) > 1 and sub[1] else []
            qs.append({"title": it[1] or "", "type": it[3], "entry": sub[0], "page": page,
                       "required": bool(sub[2]) if len(sub) > 2 else False, "options": opts})
    fb = re.search(r'name="fbzx"\s+value="([^"]+)"', html)
    return qs, (fb.group(1) if fb else "")


def split_vals(v, qtype):
    return [x.strip() for x in v.split("|") if x.strip()] if qtype == 4 else [v.strip()]


STOP = {"de", "des", "du", "la", "le", "les", "et", "ou", "a", "un", "une", "par", "avec", "en", "au",
        "que", "qui", "pour", "sur", "ce"}


def tokens(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return {t for t in re.findall(r"[a-z0-9]+", s.replace(",", ".")) if t not in STOP}


def resolve(val, options, forced=None):
    """Trouve l'option du formulaire qui correspond à val (ou None)."""
    if forced and val in forced:
        return forced[val]
    for o in options:
        if o == val:
            return o
    for o in options:
        if norm(o) == norm(val):
            return o
    for stem in SYN.get(norm(val), []):
        for o in options:
            if stem in norm(o):
                return o
    tv = tokens(val)
    if tv:
        full = [(len(tv) / len(tokens(o) | tv), o) for o in options if tv <= tokens(o)]
        if len(full) == 1:
            return full[0][1]
        if len(full) > 1:
            full.sort(reverse=True)
            if full[0][0] > full[1][0]:
                return full[0][1]
            return None
    close = difflib.get_close_matches(val, options, n=1, cutoff=0.85)
    return close[0] if close else None


def auto_mapping(qs, rows, cols):
    """Associe chaque question à une colonne CSV (par les valeurs, puis l'ordre des colonnes)."""
    distinct = {c: sorted({x for r in rows for v in [r.get(c, "")] if v.strip()
                           for x in split_vals(v, 4)}) for c in cols}
    mapping, pointer = {}, -1
    for q in qs:
        if q["type"] not in CHOICE or not q["options"]:
            continue
        scored = []
        for i, c in enumerate(cols):
            D = distinct[c]
            if not D:
                continue
            hit_vals = [d for d in D if resolve(d, q["options"])]
            hit_opts = {resolve(d, q["options"]) for d in hit_vals}
            if not hit_vals:
                continue
            score = len(hit_vals) / len(D)
            scored.append((score, i, c))
        if not scored:
            continue
        best = max(s for s, _, _ in scored)
        if best < 0.3:
            continue
        ties = [(i, c) for s, i, c in scored if s >= best - 0.02]
        after = [t for t in ties if t[0] > pointer]
        i, c = (after or ties)[0]
        mapping[q["entry"]] = {"title": q["title"], "column": c, "values": {}}
        pointer = i
    return mapping


def load_mapping(path, qs):
    raw = json.load(open(path, encoding="utf-8"))
    by_num = {}
    for q in qs:
        m = re.match(r"\s*Q(\d+)\b", q["title"])
        if m:
            by_num[f"Q{m.group(1)}"] = q["entry"]
    out = {}
    for k, v in raw.items():
        entry = by_num.get(k) or (int(k) if k.isdigit() else None)
        if entry is None:
            print(f"(mapping: clé {k} introuvable dans le formulaire, ignorée)")
            continue
        out[entry] = v
    return out


def build(qs, mapping, row, forced_by_entry):
    """Retourne (payload, sections_visitées, problèmes).
    Une section est visitée si >= 60% de ses questions mappées ont une réponse valide
    (c'est comme ça qu'on sépare consommateur / distributeur / restaurant)."""
    def answer(q):
        m = mapping.get(q["entry"])
        return (row.get(m["column"], "") if m else "").strip()

    def valid(q, val):
        if not val:
            return False
        if q["type"] in CHOICE and q["options"]:
            return all(resolve(v, q["options"], forced_by_entry.get(q["entry"]))
                       for v in split_vals(val, q["type"]))
        return True

    pages = {0}
    for p in sorted({q["page"] for q in qs} - {0}):
        pq = [q for q in qs if q["page"] == p and q["entry"] in mapping]
        if pq and sum(valid(q, answer(q)) for q in pq) / len(pq) >= 0.6:
            pages.add(p)

    payload, problems = [], []
    for q in qs:
        if q["page"] not in pages:
            continue
        val = answer(q)
        key = f"entry.{q['entry']}"
        t = q["type"]
        if not val:
            if q["required"]:
                problems.append(f"obligatoire sans réponse: « {q['title']} »")
            continue
        if t in CHOICE and q["options"]:
            for v in split_vals(val, t):
                o = resolve(v, q["options"], forced_by_entry.get(q["entry"]))
                if o is None:
                    problems.append(f"« {v} » n'est pas une option de « {q['title']} »")
                else:
                    payload.append((key, o))
        elif t == 9:
            y, mo, d = val.split("-")
            payload += [(key + "_year", y), (key + "_month", str(int(mo))), (key + "_day", str(int(d)))]
        elif t == 10:
            h, mi = val.split(":")[:2]
            payload += [(key + "_hour", str(int(h))), (key + "_minute", str(int(mi)))]
        else:
            payload.append((key, val))
    return payload, sorted(pages), problems


def judge(text):
    """Analyse la page renvoyée par Google. Retourne (statut, détail)."""
    low = text.lower()
    ok_marks = ["freebirdformviewerviewresponseconfirmationmessage", "a bien été enregistrée",
                "response has been recorded", "envoyer une autre réponse", "submit another response",
                "viewform?edit2=", "your response has been"]
    if any(m in low for m in ok_marks):
        return "ok", ""
    plain = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    err = [m for m in ["cette question est obligatoire", "this is a required question",
                        "réponse invalide", "invalid"] if m in plain.lower()]
    return ("erreur" if err else "inconnu"), f"mots repérés: {err}  | début de page: {plain[:300]}"


def read_csv(path, encoding):
    with open(path, newline="", encoding=encoding) as f:
        head = f.readline()
        f.seek(0)
        delim = ";" if head.count(";") > head.count(",") else ","
        rd = csv.DictReader(f, delimiter=delim)
        return list(rd), rd.fieldnames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--questions", action="store_true")
    ap.add_argument("--show-mapping", action="store_true")
    ap.add_argument("--save-mapping")
    ap.add_argument("--mapping")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--encoding", default="utf-8-sig")
    a = ap.parse_args()

    print("gform_fill v4")
    view, post = form_urls(a.url)
    s = requests.Session()
    qs, fbzx = parse_form(s.get(view, timeout=30).text)

    if a.questions or not a.csv:
        for q in qs:
            print(f"[section {q['page']}] {'*' if q['required'] else ' '} {q['title']}  "
                  f"[{TYPES.get(q['type'], q['type'])}]" + (f"  {q['options']}" if q["options"] else ""))
        return

    rows, fields = read_csv(a.csv, a.encoding)
    cols = [c for c in fields if norm(c) not in IGNORE_COLS]
    import os
    default_map = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mapping.json")
    if a.mapping or os.path.exists(default_map):
        mapping = load_mapping(a.mapping or default_map, qs)
    else:
        mapping = auto_mapping(qs, rows, cols)
    forced = {e: m.get("values", {}) for e, m in mapping.items()}

    if a.save_mapping:
        keyed = {}
        for q in qs:
            if q["entry"] in mapping:
                n = re.match(r"\s*Q(\d+)\b", q["title"])
                keyed[f"Q{n.group(1)}" if n else str(q["entry"])] = mapping[q["entry"]]
        json.dump(keyed, open(a.save_mapping, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"Mapping écrit dans {a.save_mapping}")
    if a.show_mapping or a.save_mapping:
        for q in qs:
            m = mapping.get(q["entry"])
            print(f"{(m['column'] if m else '??? (non mappée)'):<14} -> [section {q['page']}] {q['title']}")
        return

    ok = ko = 0
    for i, row in enumerate(rows[: a.limit] if a.limit else rows, 1):
        payload, pages, problems = build(qs, mapping, row, forced)
        if problems:
            print(f"[ligne {i}] IGNORÉE: {'; '.join(problems)}")
            ko += 1
            continue
        payload += [("pageHistory", ",".join(map(str, pages))), ("fvv", "1"), ("fbzx", fbzx),
                    ("partialResponse", json.dumps([None, None, fbzx]))]
        if a.dry_run:
            print(f"[ligne {i}] OK (dry-run) sections {pages}, {len(payload) - 4} champs")
            ok += 1
            continue
        try:
            r = s.post(post, data=payload, timeout=30, headers={"Referer": view})
            status, detail = judge(r.text) if r.status_code == 200 else ("http", f"HTTP {r.status_code}")
            good = status in ("ok", "inconnu")
            if status != "ok":
                open("reponse_google.html", "w", encoding="utf-8").write(r.text)
        except requests.RequestException as e:
            good, status, detail = False, "réseau", str(e)
        print(f"[ligne {i}] {('envoyée' if status == 'ok' else 'envoyée (probable, à vérifier dans Forms)') if good else 'ÉCHEC (' + status + ')'}")
        if not good or status == "inconnu":
            print(f"   {detail}")
        ok += bool(good)
        ko += not good
        time.sleep(a.delay)
    print(f"\nTerminé: {ok} envoyées, {ko} en échec. Compare avec le nombre de réponses dans Google Forms.")


if __name__ == "__main__":
    main()
