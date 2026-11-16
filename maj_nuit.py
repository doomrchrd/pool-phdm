import json
import os
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests

SAISON = "20262027"
GAME_TYPE = "2"
DATA_FILE = "phdm_state.json"

SLUG_MAP = {
    "Dallas Stars": "dal", "Saint-Louis Blues": "stl", "Vegas Golden Knights": "vgk",
    "Chicago Black Hawks": "chi", "Seattle Kraken": "sea", "Buffalo Sabres": "buf",
    "Philadelphia Flyers": "phi", "UCKA MOCKBA": "uck", "New-York Rangers": "nyr",
    "Vancouver Canucks": "van", "Montréal Canadiens": "mtl"
}

SPECIAL_OVERRIDE = {}
ALIASES = {
    "vilardi gabe": "gabriel vilardi", "gabe vilardi": "gabriel vilardi",
    "frederick gaudreau": "freddy gaudreau", "gaudreau frederick": "freddy gaudreau",
    "gaudreau fred": "freddy gaudreau", "fred gaudreau": "freddy gaudreau",
    "d astous charle ed": "charle edouard d astous", "d astous charle edouard": "charle edouard d astous",
    "dastous charle ed": "charle edouard d astous", "dastous charle edouard": "charle edouard d astous",
    "charles edouard d astous": "charle edouard d astous", "dubois pierre luc": "pierre luc dubois",
    "brandsegg nygard": "michael brandsegg nygard", "sharangovich egor": "yegor sharangovich",
    "sharangovich yegor": "yegor sharangovich", "dach kale": "kirby dach", "dach kirby": "kirby dach",
    "miller jt": "jt miller", "miller j t": "jt miller", "compher jt": "jt compher",
    "compher j t": "jt compher", "greer aj": "aj greer", "pageau jg": "jean gabriel pageau",
    "peterka j j": "jj peterka", "peterka jj": "jj peterka", "hughes t j": "tj hughes",
    "oshie t j": "tj oshie", "oshie tj": "tj oshie", "emery e j": "ej emery", "paul nicolas": "nick paul",
    "trocheck vince": "vincent trocheck", "wilson thomas": "tom wilson", "deangelo anthony": "tony deangelo",
    "nugent hop ryan": "ryan nugent hopkins", "ekman larsson o": "oliver ekman larsson",
    "vanriemsdyk trevo": "trevor van riemsdyk", "andersen fred": "frederik andersen",
    "wennberg alex": "alexander wennberg", "gallagher branden": "brendan gallagher",
    "rust brian": "bryan rust", "seguin taylor": "tyler seguin", "letang khris": "kris letang",
    "vanriemsdyk james": "james van riemsdyk", "ovechkin alex": "alexander ovechkin",
    "kuznetsov evgeni": "evgeny kuznetsov", "varlamov semeon": "semyon varlamov",
    "toropchenko alexe": "alexei toropchenko", "mukhamadullin sha": "shakir mukhamadullin",
    "stephenson chandl": "chandler stephenson", "gostisbehere s": "shayne gostisbehere",
    "blackwood mack": "mackenzie blackwood", "hughes quinton": "quinn hughes",
    "khusnutdinov mara": "marat khusnutdinov", "killorn alexander": "alex killorn",
    "huberdeau jonatha": "jonathan huberdeau", "slukynsky hamptor": "hampton slukynsky",
    "lorenz reiger": "rieger lorenz", "novak tom": "thomas novak", "smith william": "will smith",
    "boldy matthew": "matt boldy", "humphreys christia": "christian humphreys",
    "kaskimaki aleksant": "aleksanteri kaskimaki", "romanov alex": "alexander romanov",
    "samuelsson mattia": "mattias samuelsson", "daccord joel": "joey daccord", "blueger teodors": "teddy blueger",
    "zuccarello matts": "mats zuccarello", "crosby sydney": "sidney crosby", "borgen william": "will borgen",
    "sandin pellikka axe": "axel sandin pellikka", "moser janis jerom": "janis moser",
    "luukkonen u k": "ukko pekka luukkonen", "lekkerimaki jonat": "jonathan lekkerimaki", "stutzle tim": "tim stuetzle"
}

def clean_str(s):
    if not isinstance(s, str): return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return " ".join("".join([c for c in nfkd if not unicodedata.combining(c)]).lower().replace(".", "").replace("-", " ").replace("'", " ").strip().split())

def fetch_nhl_stats():
    headers = {"User-Agent": "Mozilla/5.0"}
    skaters, goalies = {}, {}
    url_sk = f"https://api.nhle.com/stats/rest/en/skater/summary?limit=-1&cayenneExp=seasonId={SAISON}%20and%20gameTypeId={GAME_TYPE}"
    r_sk = requests.get(url_sk, headers=headers, timeout=15)
    if r_sk.status_code == 200:
        for p in r_sk.json().get("data", []):
            nc = clean_str(p.get("skaterFullName", ""))
            skaters.setdefault(nc, []).append({"pj": p.get("gamesPlayed", 0), "pts": p.get("points", 0), "team": p.get("teamAbbrevs", ""), "age": None, "posCode": p.get("positionCode", "")})

    url_sk_bio = f"https://api.nhle.com/stats/rest/en/skater/bios?limit=-1&cayenneExp=seasonId={SAISON}%20and%20gameTypeId={GAME_TYPE}"
    r_sk_bio = requests.get(url_sk_bio, headers=headers, timeout=15)
    if r_sk_bio.status_code == 200:
        today = datetime.now().date()
        for p in r_sk_bio.json().get("data", []):
            nc = clean_str(p.get("skaterFullName", ""))
            if nc in skaters and p.get("birthDate"):
                try:
                    b_dt = datetime.strptime(p.get("birthDate"), "%Y-%m-%d").date()
                    age = today.year - b_dt.year - ((today.month, today.day) < (b_dt.month, b_dt.day))
                    pos = p.get("positionCode", "")
                    for sk in skaters[nc]:
                        if sk.get("posCode") == pos or not sk.get("posCode"):
                            sk["age"] = age
                except Exception:
                    pass

    url_g = f"https://api.nhle.com/stats/rest/en/goalie/summary?limit=-1&cayenneExp=seasonId={SAISON}%20and%20gameTypeId={GAME_TYPE}"
    r_g = requests.get(url_g, headers=headers, timeout=15)
    if r_g.status_code == 200:
        for g in r_g.json().get("data", []):
            v, otl, so = g.get("wins", 0), g.get("otLosses", 0), g.get("shutouts", 0)
            nc = clean_str(g.get("goalieFullName", ""))
            goalies.setdefault(nc, []).append({"pj": g.get("gamesPlayed", 0), "pts": (v * 2) + (otl * 1) + (so * 1), "team": g.get("teamAbbrevs", ""), "age": None, "posCode": "G"})

    url_g_bio = f"https://api.nhle.com/stats/rest/en/goalie/bios?limit=-1&cayenneExp=seasonId={SAISON}%20and%20gameTypeId={GAME_TYPE}"
    r_g_bio = requests.get(url_g_bio, headers=headers, timeout=15)
    if r_g_bio.status_code == 200:
        today = datetime.now().date()
        for g in r_g_bio.json().get("data", []):
            nc = clean_str(g.get("goalieFullName", ""))
            if nc in goalies and g.get("birthDate"):
                try:
                    b_dt = datetime.strptime(g.get("birthDate"), "%Y-%m-%d").date()
                    age = today.year - b_dt.year - ((today.month, today.day) < (b_dt.month, b_dt.day))
                    for go in goalies[nc]:
                        go["age"] = age
                except Exception:
                    pass
    return skaters, goalies

def get_player_live_stats(nhl_skaters, nhl_goalies, nom_excel, team_defaut, age_defaut, pos_cat):
    nom_clean = clean_str(nom_excel)
    if nom_clean in SPECIAL_OVERRIDE:
        ov = SPECIAL_OVERRIDE[nom_clean]
        return (ov["team"] or team_defaut), ov.get("age", age_defaut), ov["pj"], ov["pts"]
    db = nhl_goalies if pos_cat == "GARD" else nhl_skaters
    def pick_best(plist):
        if not plist: return None
        if len(plist) == 1: return plist[0]
        for p in plist:
            pc = p.get("posCode", "")
            if pos_cat == "DEF" and pc == "D": return p
            if pos_cat == "ATT" and pc in ["C", "L", "R", "F", "LW", "RW"]: return p
        return plist[0]
    for cand in [ALIASES.get(nom_clean, ""), nom_clean]:
        if cand and cand in db:
            m = pick_best(db[cand])
            if m: return m["team"] or team_defaut, m.get("age") or age_defaut, m["pj"], m["pts"]
    parts = nom_clean.split()
    if len(parts) >= 3:
        n2 = f"{' '.join(parts[1:])} {parts[0]}"
        if n2 in db:
            m = pick_best(db[n2])
            if m: return m["team"] or team_defaut, m.get("age") or age_defaut, m["pj"], m["pts"]
    if len(parts) >= 2:
        n3 = f"{parts[-1]} {' '.join(parts[:-1])}"
        if n3 in db:
            m = pick_best(db[n3])
            if m: return m["team"] or team_defaut, m.get("age") or age_defaut, m["pj"], m["pts"]
    return team_defaut, age_defaut, 0, 0

def main():
    if not os.path.exists(DATA_FILE):
        print("phdm_state.json introuvable sur GitHub : faites d'abord le clic sur ACTUALISER API sur le site.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        state_data = json.load(f)

    skaters, goalies = fetch_nhl_stats()
    if not skaters and not goalies:
        print("API LNH vide ou inaccessible, mise à jour annulée pour sécurité.")
        return

    rosters = state_data.get("rosters", {})
    for slug in SLUG_MAP.values():
        if slug not in rosters:
            continue
        for role, pos_cat in [("att", "ATT"), ("def", "DEF"), ("gard", "GARD"), ("banc_att", "ATT"), ("banc_def", "DEF"), ("banc_gard", "GARD")]:
            records = rosters[slug].get(role, [])
            if not records:
                continue
            df = pd.DataFrame(records)
            for idx, row in df.iterrows():
                t_r, age_r, pj_r, pts_r = get_player_live_stats(skaters, goalies, str(row.get("Joueurs", "")), str(row.get("T", "")), row.get("A", 0), pos_cat)
                df.at[idx, "T"] = t_r
                df.at[idx, "A"] = age_r
                df.at[idx, "PJ"] = pj_r
                df.at[idx, "Pts"] = pts_r
            df = df.sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
            df["No"] = range(1, len(df) + 1)
            rosters[slug][role] = df.to_dict(orient="records")

        for pos, max_p in [("att", 25), ("def", 11), ("gard", 4)]:
            df_p = pd.DataFrame(rosters[slug].get(pos, []))
            df_b = pd.DataFrame(rosters[slug].get(f"banc_{pos}", []))
            if not df_p.empty and len(df_p) > max_p:
                df_p = df_p.sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                demoted = df_p.iloc[max_p:].copy()
                df_p = df_p.iloc[:max_p].copy()
                df_b = pd.concat([df_b, demoted], ignore_index=True).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                df_p["No"] = range(1, len(df_p) + 1)
                df_b["No"] = range(1, len(df_b) + 1)
                rosters[slug][pos] = df_p.to_dict(orient="records")
                rosters[slug][f"banc_{pos}"] = df_b.to_dict(orient="records")

    state_data["last_official_update_et"] = datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d %H:%M")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=4)
    print(f"Mise à jour officielle terminée à {state_data['last_official_update_et']} HE.")

if __name__ == "__main__":
    main()
