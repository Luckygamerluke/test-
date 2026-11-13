#!/usr/bin/env python3
"""Génère un CSV de réponses FICTIVES (pour s'entraîner aux statistiques), même format que
reponses_entrainement.csv. Python standard uniquement, pas besoin de numpy.

Les réponses ne sont pas indépendantes: chaque répondant a une "sensibilité" cachée qui influence
plusieurs réponses (naturel, image, intention d'achat, prix...). Tu peux donc y trouver de vraies
corrélations à tester (chi², Spearman, régression...). L'ordre des lignes est mélangé.

  python gen_synthetic.py                          # 67 consommateurs / 21 distributeurs / 18 restaurants
  python gen_synthetic.py -c 150 -d 30 -r 25 --seed 3 -o synthetique_2.csv

Ces données sont FICTIVES: ne les présente jamais comme de vraies réponses d'enquête.
"""
import argparse
import csv
import random

COLS = ["id", "profile", "freq", "juice", "cor", "natural", "image", "intent", "price", "channel",
        "cert", "past", "open", "criteria", "partnership", "difficulties", "collab", "offer",
        "interest", "arguments", "origin"]


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def ordinal(z, options, slope=0.8, noise=0.8, center=None):
    """Choisit une option d'une liste ordonnée (du plus faible au plus fort) selon z + bruit."""
    c = (len(options) - 1) / 2 if center is None else center
    return options[int(clamp(round(c + slope * z + random.gauss(0, noise)), 0, len(options) - 1))]


def scale(z, slope=0.9, noise=0.8, center=3.2):
    return str(int(clamp(round(center + slope * z + random.gauss(0, noise)), 1, 5)))


def pick_many(options, weights, kmin, kmax):
    k = random.randint(kmin, kmax)
    chosen, pool, w = [], list(options), list(weights)
    for _ in range(min(k, len(pool))):
        i = random.choices(range(len(pool)), weights=w)[0]
        chosen.append(pool.pop(i))
        w.pop(i)
    return " | ".join(o for o in options if o in chosen)  # ordre stable


def known_image(z):
    if random.random() < 0.08:
        return "Je ne connais pas"
    return ordinal(z, ["Plutôt négative", "Neutre", "Plutôt positive", "Très positive"],
                   slope=0.9, noise=0.7, center=1.7)


def consumer():
    z = random.gauss(0, 1)
    juices = ["Jus industriels en grande surface", "Jus frais locaux",
              "Jus bio ou naturels sans sucres ajoutés", "Smoothies"]
    jw = [2.2 - 0.5 * z, 1.4 + 0.2 * z, 1.0 + 0.8 * z, 0.8 + 0.3 * z]
    return {
        "freq": random.choices(["Rarement ou jamais", "Occasionnellement", "Plusieurs fois par semaine",
                                "Tous les jours"], weights=[15, 38, 32, 15])[0],
        "juice": pick_many(juices, [max(w, 0.1) for w in jw], 1, 3),
        "cor": ordinal(z * 0.4, ["Je ne connais pas", "Entendu parler, jamais goûté", "J'en ai déjà goûté",
                                 "J'en consomme régulièrement"], slope=0.6, noise=0.9, center=1.4),
        "natural": scale(z),
        "image": known_image(z),
        "intent": ordinal(z, ["Non", "Peut-être, besoin d'informations", "Oui, si le prix est raisonnable",
                              "Oui, sans hésitation"], slope=0.9, noise=0.7, center=1.5),
        "price": ordinal(z, ["Moins de 2 €", "Entre 2 et 3 €", "Entre 3 et 4 €", "Plus de 4 €"],
                         slope=0.55, noise=0.7, center=1.0),
        "channel": random.choices(["Grande surface", "Épicerie fine / boutique bio", "Marché local",
                                   "En ligne", "Restaurant / café"],
                                  weights=[34, 18 + 4 * z, 22, 11, 13])[0],
        "cert": ordinal(z, ["Je ne sais pas", "Aucun changement", "Un plus, pas obligatoire", "Indispensable"],
                        slope=0.7, noise=0.8, center=1.6),
    }


def distributor():
    z = random.gauss(0, 1)
    crit = ["Certification sanitaire (HACCP / ISO 22000)", "Prix compétitif", "Volumes garantis",
            "Régularité des approvisionnements", "Origine et traçabilité", "Différenciation"]
    diff = ["Certifications sanitaires européennes", "Coûts logistiques et fret", "Volumes insuffisants",
            "Notoriété à construire", "Barrières réglementaires"]
    return {
        "image": known_image(z),
        "past": ordinal(z, ["Non, jamais", "En cours de prospection", "Oui, ponctuellement",
                            "Oui, régulièrement"], slope=0.8, noise=0.9, center=1.2),
        "open": scale(z, center=3.6),
        "criteria": pick_many(crit, [3, 4 + z, 2.5, 3, 2, 2.5], 2, 5),
        "partnership": scale(z * 0.7, center=3.5, noise=1.0),
        "difficulties": pick_many(diff, [3, 3.5, 2.5, 2.5, 1.5], 1, 4),
        "collab": random.choices(["Agent / transitaire local", "Distribution exclusive", "Achat ponctuel",
                                  "Importation directe"], weights=[40, 22, 22, 16])[0],
    }


def restaurant():
    z = random.gauss(0, 1)
    args = ["Certification sanitaire européenne", "Naturel / sans sucres ajoutés",
            "Exotisme et différenciation", "Prix compétitif", "Packaging soigné", "Histoire de la marque"]
    return {
        "offer": ordinal(z, ["Non, pas encore", "Oui, occasionnellement", "Oui, régulièrement"],
                         slope=0.6, noise=0.8, center=0.9),
        "cor": ordinal(z * 0.5, ["Jamais entendu parler", "J'en ai entendu parler", "Je le connais bien"],
                       slope=0.6, noise=0.8, center=1.1),
        "interest": scale(z, center=3.5),
        "price": ordinal(z, ["Moins de 1,50 €", "Entre 1,50 et 2,50 €", "Entre 2,50 et 3,50 €",
                             "Plus de 3,50 €"], slope=0.45, noise=0.7, center=1.0),
        "arguments": pick_many(args, [3, 3.5 + z, 3, 3.5, 2.5, 2], 1, 5),
        "origin": scale(z * 0.6, center=3.4, noise=1.0),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--consommateurs", type=int, default=67)
    ap.add_argument("-d", "--distributeurs", type=int, default=21)
    ap.add_argument("-r", "--restaurants", type=int, default=18)
    ap.add_argument("--seed", type=int, default=None, help="pour retrouver exactement les mêmes données")
    ap.add_argument("-o", "--output", default="synthetique.csv")
    a = ap.parse_args()
    if a.seed is not None:
        random.seed(a.seed)

    rows = ([("consumer", consumer()) for _ in range(a.consommateurs)]
            + [("distributor", distributor()) for _ in range(a.distributeurs)]
            + [("restaurant", restaurant()) for _ in range(a.restaurants)])
    random.shuffle(rows)  # les gens répondent dans le désordre
    with open(a.output, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writerow(COLS)
        for i, (prof, d) in enumerate(rows, 1):
            w.writerow([i, prof] + [d.get(c, "") for c in COLS[2:]])
    print(f"{len(rows)} réponses FICTIVES écrites dans {a.output} "
          f"({a.consommateurs}/{a.distributeurs}/{a.restaurants})")


if __name__ == "__main__":
    main()
