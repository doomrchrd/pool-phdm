import streamlit as st
import pandas as pd
import requests
import unicodedata
import json
import os
import re
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

st.set_page_config(
    page_title="PHDM - Plateforme Officielle Multi-Équipes",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# =========================================================
# VÉRIFICATION DU MOT DE PASSE COMMISSAIRE
# =========================================================
MOT_DE_PASSE_SECRET = "phdm2026"  # 👈 Mot de passe secret pour PHDM

with st.sidebar:
    st.markdown("### 🔐 Accès Direction")
    mdp_entre = st.text_input("Mot de passe commissaire :", type="password")
    is_admin = (mdp_entre == MOT_DE_PASSE_SECRET)

    if is_admin:
        st.success("🟢 Mode Commissaire Déverrouillé")
    elif mdp_entre != "":
        st.error("Mot de passe incorrect")

# =========================================================
# 1. OUTILS DE NETTOYAGE INTELLIGENTS & FORMATAGE
# =========================================================
MOIS_FR = {
    1: "janv", 2: "févr", 3: "mars", 4: "avr", 5: "mai", 6: "juin",
    7: "juil", 8: "août", 9: "sept", 10: "oct", 11: "nov", 12: "déc"
}

# Cohortes actives pour le repêchage (2027 et 2028)
DRAFT_YEARS = ["2027", "2028"]

def format_date_fr(val):
    if pd.isna(val): return ""
    val_str = str(val).strip()
    if val_str.lower() in ["nan", "none", "", "nat", " "]: return ""
    try:
        dt = pd.to_datetime(val)
        return f"{dt.day} {MOIS_FR.get(dt.month, '')} {dt.year}".strip()
    except Exception:
        return val_str.split("T")[0].split(" ")[0]

def clean_str(s):
    if not isinstance(s, str): return ""
    nfkd = unicodedata.normalize('NFKD', s)
    return " ".join("".join([c for c in nfkd if not unicodedata.combining(c)]).lower().replace(".", "").replace("-", " ").replace("'", " ").strip().split())

def clean_excel_dataframe(df):
    if df is None or df.empty: return pd.DataFrame()
    df = df.copy()
    unnamed_cols = [c for c in df.columns if str(c).startswith("Unnamed")]
    if len(unnamed_cols) >= max(1, len(df.columns) - 2):
        for idx, r in df.head(15).iterrows():
            vals_str = [str(v).lower().strip() for v in r.values if pd.notna(v)]
            if any(k in v for v in vals_str for k in ["equipe", "équipe", "date", "joueur", "choix", "ronde", "article", "reglement"]):
                new_headers = []
                for i, val in enumerate(r.values):
                    if pd.notna(val) and str(val).strip() != "": new_headers.append(str(val).strip())
                    else: new_headers.append(f"Col_{i+1}")
                df.columns = new_headers
                df = df.iloc[idx + 1:].reset_index(drop=True)
                break
    cols_to_keep = []
    for i in range(len(df.columns)):
        c = df.columns[i]
        series_str = df.iloc[:, i].dropna().astype(str).str.strip()
        valeurs_reelles = series_str[~series_str.str.lower().isin(["", "nan", "none"])]
        if str(c).startswith("Unnamed") and len(valeurs_reelles) == 0: continue
        cols_to_keep.append(i)
    if cols_to_keep: df = df.iloc[:, cols_to_keep].copy()
    clean_cols = []
    seen = set()
    for i, c in enumerate(df.columns):
        base_name = f"Info {i+1}" if str(c).startswith("Unnamed") else str(c)
        final_name = base_name
        counter = 1
        while final_name in seen:
            final_name = f"{base_name} ({counter})"
            counter += 1
        seen.add(final_name)
        clean_cols.append(final_name)
    df.columns = clean_cols
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.fillna(" ")
    for col in df.columns:
        df[col] = (df[col].astype(str).str.replace('""', ' ', regex=False).str.replace("''", ' ', regex=False).str.replace('NaN', ' ', regex=False).str.replace('nan', ' ', regex=False).str.replace('None', ' ', regex=False))
    return df

def color_empty_cells(val):
    if str(val).strip() == "": return 'background-color: #0F172A; color: #0F172A;'
    return 'background-color: #1E293B; color: #E2E8F0;'

# =========================================================
# 2. STATS OFFICIELLES LNH ET ÂGE EN DIRECT
# =========================================================
SAISON = "20262027"
GAME_TYPE = "2"

@st.cache_data(ttl=300, show_spinner=False)
def _fetch_nhl_stats_cached():
    import time
    from concurrent.futures import ThreadPoolExecutor
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    skaters, goalies = {}, {}
    today = datetime.now().date()

    def get_with_retry(url, params=None, timeout=15, retries=3):
        last_err = None
        for attempt in range(retries):
            try:
                r = session.get(url, params=params, timeout=timeout)
                if r.status_code == 200:
                    return r
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(0.8 * (attempt + 1))
                    continue
                r.raise_for_status()
            except Exception as e:
                last_err = e
                time.sleep(0.8 * (attempt + 1))
        if last_err:
            raise last_err
        raise RuntimeError(f"Échec HTTP sur {url}")

    def fetch_all_pages(endpoint):
        url = f"https://api.nhle.com/stats/rest/en/{endpoint}"
        # Plan A1 : tout récupérer en 1 requête (limit=-1)
        try:
            params_all = {
                "limit": -1,
                "start": 0,
                "sort": '[{"property":"playerId","direction":"ASC"}]',
                "cayenneExp": f"seasonId={SAISON} and gameTypeId={GAME_TYPE}"
            }
            r0 = session.get(url, params=params_all, timeout=12)
            if r0.status_code == 200:
                p0 = r0.json()
                b0 = p0.get("data", [])
                t0 = p0.get("total") or len(b0)
                if b0 and len(b0) >= t0 and (len(b0) > 100 or "goalie" in endpoint):
                    return b0, t0
        except Exception:
            pass

        # Plan A2 : pagination par blocs de 100 avec réessai automatique
        rows = []
        seen_ids = set()
        start = 0
        limit = 100
        api_total = 0
        while True:
            params = {
                "start": start,
                "limit": limit,
                "sort": '[{"property":"playerId","direction":"ASC"}]',
                "cayenneExp": f"seasonId={SAISON} and gameTypeId={GAME_TYPE}"
            }
            r = get_with_retry(url, params=params, timeout=15, retries=3)
            payload = r.json()
            batch = payload.get("data", [])
            if payload.get("total"):
                api_total = payload["total"]
            if not batch:
                break
            for item in batch:
                pid = item.get("playerId")
                if pid is not None:
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                rows.append(item)
            start += len(batch)
            if api_total and start >= api_total:
                break
            if len(batch) < limit and not api_total:
                break
        return rows, (api_total or len(rows))

    def fetch_via_club_stats():
        # Plan B : récupère les 32 équipes via api-web.nhle.com (cadence modérée anti-blocage)
        sk_by_pid, g_by_pid = {}, {}
        team_abbrevs = [
            "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL",
            "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NJD",
            "NSH", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS",
            "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WPG", "WSH"
        ]
        try:
            rs = get_with_retry("https://api-web.nhle.com/v1/standings/now", timeout=10, retries=2)
            st_list = rs.json().get("standings", [])
            found = [x.get("teamAbbrev", {}).get("default") for x in st_list if x.get("teamAbbrev", {}).get("default")]
            if len(found) >= 30:
                team_abbrevs = found
        except Exception:
            pass

        def get_team_data(tm):
            cs_data, ros_data = {}, {}
            try:
                cs_data = get_with_retry(f"https://api-web.nhle.com/v1/club-stats/{tm}/{SAISON}/{GAME_TYPE}", timeout=12, retries=2).json()
            except Exception:
                pass
            try:
                ros_data = get_with_retry(f"https://api-web.nhle.com/v1/roster/{tm}/{SAISON}", timeout=12, retries=2).json()
            except Exception:
                pass
            return tm, cs_data, ros_data

        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(get_team_data, team_abbrevs))

        age_by_pid = {}
        for tm, cs_data, ros_data in results:
            for grp in ("forwards", "defensemen", "goalies"):
                for rp in ros_data.get(grp, []):
                    pid = rp.get("id")
                    bdate = rp.get("birthDate")
                    if pid and bdate:
                        try:
                            b_dt = datetime.strptime(bdate, "%Y-%m-%d").date()
                            age_by_pid[pid] = today.year - b_dt.year - ((today.month, today.day) < (b_dt.month, b_dt.day))
                        except Exception:
                            pass

            for p in cs_data.get("skaters", []):
                pid = p.get("playerId")
                fn = p.get("firstName", {}).get("default", "") if isinstance(p.get("firstName"), dict) else str(p.get("firstName", ""))
                ln = p.get("lastName", {}).get("default", "") if isinstance(p.get("lastName"), dict) else str(p.get("lastName", ""))
                full = clean_str(f"{fn} {ln}")
                if not full:
                    continue
                key = pid or full
                if key not in sk_by_pid:
                    sk_by_pid[key] = {
                        "name": full, "pj": 0, "pts": 0, "teams": [],
                        "age": age_by_pid.get(pid), "posCode": p.get("positionCode", "")
                    }
                sk_by_pid[key]["pj"] += int(p.get("gamesPlayed", 0) or 0)
                sk_by_pid[key]["pts"] += int(p.get("points", 0) or 0)
                if tm not in sk_by_pid[key]["teams"]:
                    sk_by_pid[key]["teams"].append(tm)
                if sk_by_pid[key]["age"] is None and pid in age_by_pid:
                    sk_by_pid[key]["age"] = age_by_pid[pid]

            for g in cs_data.get("goalies", []):
                pid = g.get("playerId")
                fn = g.get("firstName", {}).get("default", "") if isinstance(g.get("firstName"), dict) else str(g.get("firstName", ""))
                ln = g.get("lastName", {}).get("default", "") if isinstance(g.get("lastName"), dict) else str(g.get("lastName", ""))
                full = clean_str(f"{fn} {ln}")
                if not full:
                    continue
                v = int(g.get("wins", 0) or 0)
                otl = int(g.get("overtimeLosses", g.get("otLosses", 0)) or 0)
                so = int(g.get("shutouts", 0) or 0)
                key = pid or full
                if key not in g_by_pid:
                    g_by_pid[key] = {
                        "name": full, "pj": 0, "pts": 0, "teams": [],
                        "age": age_by_pid.get(pid), "posCode": "G"
                    }
                g_by_pid[key]["pj"] += int(g.get("gamesPlayed", 0) or 0)
                g_by_pid[key]["pts"] += (v * 2) + (otl * 1) + (so * 1)
                if tm not in g_by_pid[key]["teams"]:
                    g_by_pid[key]["teams"].append(tm)
                if g_by_pid[key]["age"] is None and pid in age_by_pid:
                    g_by_pid[key]["age"] = age_by_pid[pid]

        sk_out, g_out = {}, {}
        for rec in sk_by_pid.values():
            sk_out.setdefault(rec["name"], []).append({
                "pj": rec["pj"], "pts": rec["pts"],
                "team": ", ".join(rec["teams"]), "age": rec["age"], "posCode": rec["posCode"]
            })
        for rec in g_by_pid.values():
            g_out.setdefault(rec["name"], []).append({
                "pj": rec["pj"], "pts": rec["pts"],
                "team": ", ".join(rec["teams"]), "age": rec["age"], "posCode": "G"
            })
        return sk_out, g_out

    total_sk_api, total_g_api = 0, 0
    try:
        # 1. Patineurs (stats)
        sk_rows, total_sk_api = fetch_all_pages("skater/summary")
        for p in sk_rows:
            nom_clean = clean_str(p.get("skaterFullName", ""))
            if nom_clean not in skaters:
                skaters[nom_clean] = []
            skaters[nom_clean].append({
                "pj": p.get("gamesPlayed", 0),
                "pts": p.get("points", 0),
                "team": p.get("teamAbbrevs", ""),
                "age": None,
                "posCode": p.get("positionCode", "")
            })

        # 2. Patineurs (bios / âge)
        try:
            sk_bio_rows, _ = fetch_all_pages("skater/bios")
            for p in sk_bio_rows:
                nom_clean = clean_str(p.get("skaterFullName", ""))
                if nom_clean in skaters and p.get("birthDate"):
                    try:
                        b_dt = datetime.strptime(p.get("birthDate"), "%Y-%m-%d").date()
                        age = today.year - b_dt.year - ((today.month, today.day) < (b_dt.month, b_dt.day))
                        pos = p.get("positionCode", "")
                        for sk in skaters[nom_clean]:
                            if sk.get("posCode") == pos or not sk.get("posCode"):
                                sk["age"] = age
                    except Exception:
                        pass
        except Exception:
            pass

        # 3. Gardiens (stats)
        g_rows, total_g_api = fetch_all_pages("goalie/summary")
        for g in g_rows:
            v, otl, so = g.get("wins", 0), g.get("otLosses", 0), g.get("shutouts", 0)
            nom_clean = clean_str(g.get("goalieFullName", ""))
            if nom_clean not in goalies:
                goalies[nom_clean] = []
            goalies[nom_clean].append({
                "pj": g.get("gamesPlayed", 0),
                "pts": (v * 2) + (otl * 1) + (so * 1),
                "team": g.get("teamAbbrevs", ""),
                "age": None,
                "posCode": "G"
            })

        # 4. Gardiens (bios / âge)
        try:
            g_bio_rows, _ = fetch_all_pages("goalie/bios")
            for g in g_bio_rows:
                nom_clean = clean_str(g.get("goalieFullName", ""))
                if nom_clean in goalies and g.get("birthDate"):
                    try:
                        b_dt = datetime.strptime(g.get("birthDate"), "%Y-%m-%d").date()
                        age = today.year - b_dt.year - ((today.month, today.day) < (b_dt.month, b_dt.day))
                        for go in goalies[nom_clean]:
                            go["age"] = age
                    except Exception:
                        pass
        except Exception:
            pass

    except Exception:
        pass

    total_pat = sum(len(v) for v in skaters.values())
    total_gard = sum(len(v) for v in goalies.values())

    # Sécurité anti-coupure : si < 400 patineurs ou < 40 gardiens, bascule sur les 32 équipes
    if total_pat < 400 or total_gard < 40:
        sk_b, g_b = fetch_via_club_stats()
        if sum(len(v) for v in sk_b.values()) > total_pat:
            skaters = sk_b
            total_pat = sum(len(v) for v in skaters.values())
            total_sk_api = total_pat
        if sum(len(v) for v in g_b.values()) > total_gard:
            goalies = g_b
            total_gard = sum(len(v) for v in goalies.values())
            total_g_api = total_gard

    # Empêche Streamlit de mettre en cache une réponse bloquée à 100 joueurs
    if total_pat < 400 or total_gard < 40:
        raise RuntimeError(f"Données LNH incomplètes ({total_pat} patineurs, {total_gard} gardiens)")

    return skaters, goalies, f"Connecté à la LNH : {total_pat}/{total_sk_api} patineurs et {total_gard}/{total_g_api} gardiens."

def fetch_nhl_stats():
    try:
        return _fetch_nhl_stats_cached()
    except Exception as e:
        return {}, {}, f"⚠️ API LNH temporairement incomplète ({e}) — scores sauvegardés conservés."

nhl_skaters, nhl_goalies, api_status = fetch_nhl_stats()
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

def get_player_live_stats(nom_excel, team_defaut, age_defaut, pos_cat):
    nom_clean = clean_str(nom_excel)
    if nom_clean in SPECIAL_OVERRIDE:
        return (SPECIAL_OVERRIDE[nom_clean]["team"] or team_defaut), SPECIAL_OVERRIDE[nom_clean].get("age", age_defaut), SPECIAL_OVERRIDE[nom_clean]["pj"], SPECIAL_OVERRIDE[nom_clean]["pts"]
    is_goalie = (pos_cat == "GARD")
    db = nhl_goalies if is_goalie else nhl_skaters
    def pick_best(plist):
        if not plist: return None
        if len(plist) == 1: return plist[0]
        for p in plist:
            pc = p.get("posCode", "")
            if pos_cat == "DEF" and pc == "D": return p
            if pos_cat == "ATT" and pc in ["C", "L", "R", "F", "LW", "RW"]: return p
        return plist[0]
    if nom_clean in ALIASES and ALIASES[nom_clean] in db:
        match = pick_best(db[ALIASES[nom_clean]])
        if match: return match["team"] or team_defaut, match.get("age") or age_defaut, match["pj"], match["pts"]
    if nom_clean in db:
        match = pick_best(db[nom_clean])
        if match: return match["team"] or team_defaut, match.get("age") or age_defaut, match["pj"], match["pts"]
    parts = nom_clean.split()
    if len(parts) >= 3:
        n2 = f"{' '.join(parts[1:])} {parts[0]}"
        if n2 in db:
            match = pick_best(db[n2])
            if match: return match["team"] or team_defaut, match.get("age") or age_defaut, match["pj"], match["pts"]
    if len(parts) >= 2:
        n3 = f"{parts[-1]} {' '.join(parts[:-1])}"
        if n3 in db:
            match = pick_best(db[n3])
            if match: return match["team"] or team_defaut, match.get("age") or age_defaut, match["pj"], match["pts"]
    return team_defaut, age_defaut, 0, 0

# =========================================================
# 3. ÉQUIPES & DIRECTEURS GÉNÉRAUX (PHDM)
# =========================================================
POOLERS = {
    "Dallas Stars": "Dominic Richard",
    "Saint-Louis Blues": "Stéphane Moisan",
    "Vegas Golden Knights": "Alain Leclerc",
    "Chicago Black Hawks": "Carl Vigneault",
    "Seattle Kraken": "Stéphane Nadeau",
    "Buffalo Sabres": "Jean-Pierre Marois",
    "Philadelphia Flyers": "Serge Benoit",
    "UCKA MOCKBA": "Long Bui",
    "New-York Rangers": "Dany Chabot",
    "Vancouver Canucks": "Alex Couture",
    "Montréal Canadiens": "Gérard Blanchard"
}

SLUG_MAP = {
    "Dallas Stars": "dal", "Saint-Louis Blues": "stl", "Vegas Golden Knights": "vgk",
    "Chicago Black Hawks": "chi", "Seattle Kraken": "sea", "Buffalo Sabres": "buf",
    "Philadelphia Flyers": "phi", "UCKA MOCKBA": "uck", "New-York Rangers": "nyr",
    "Vancouver Canucks": "van", "Montréal Canadiens": "mtl"
}

TEAM_ABBR_MAP = {
    "Dallas Stars": "DAL", "Saint-Louis Blues": "STL", "Vegas Golden Knights": "VGK",
    "Chicago Black Hawks": "CHI", "Seattle Kraken": "SEA", "Buffalo Sabres": "BUF",
    "Philadelphia Flyers": "PHI", "UCKA MOCKBA": "MCW", "New-York Rangers": "NYR",
    "Vancouver Canucks": "VAN", "Montréal Canadiens": "MTL"
}

ABBR_TO_TEAM = {v: k for k, v in TEAM_ABBR_MAP.items()}
ABBR_TO_TEAM["UCK"] = "UCKA MOCKBA"
ABBR_TO_TEAM["MCW"] = "UCKA MOCKBA"

TEAM_LOGOS = {
    "Dallas Stars": "https://assets.nhle.com/logos/nhl/svg/DAL_light.svg",
    "Saint-Louis Blues": "https://assets.nhle.com/logos/nhl/svg/STL_light.svg",
    "Vegas Golden Knights": "https://assets.nhle.com/logos/nhl/svg/VGK_light.svg",
    "Chicago Black Hawks": "https://assets.nhle.com/logos/nhl/svg/CHI_light.svg",
    "Seattle Kraken": "https://assets.nhle.com/logos/nhl/svg/SEA_light.svg",
    "Buffalo Sabres": "https://assets.nhle.com/logos/nhl/svg/BUF_light.svg",
    "Philadelphia Flyers": "https://assets.nhle.com/logos/nhl/svg/PHI_light.svg",
    "UCKA MOCKBA": "https://upload.wikimedia.org/wikipedia/en/thumb/0/0f/HC_CSKA_Moscow_logo.svg/200px-HC_CSKA_Moscow_logo.svg.png",
    "New-York Rangers": "https://assets.nhle.com/logos/nhl/svg/NYR_light.svg",
    "Vancouver Canucks": "https://assets.nhle.com/logos/nhl/svg/VAN_light.svg",
    "Montréal Canadiens": "https://assets.nhle.com/logos/nhl/svg/MTL_light.svg"
}

def to_team_abbr(val):
    if not val: return None
    s = clean_str(str(val))
    s_upper = str(val).strip().upper()

    if s_upper in TEAM_ABBR_MAP.values(): return s_upper
    if s_upper in ["UCK", "UCKA", "MCW"]: return "MCW"

    words = s.split()
    if "van" in words or any(w in s for w in ["vancouver", "canuck", "canucks"]): return "VAN"
    if "mcw" in words or "uck" in words or any(w in s for w in ["ucka", "mockba"]): return "MCW"
    if "chi" in words or any(w in s for w in ["chicago", "black hawk", "blackhawks", "hawks"]): return "CHI"
    if "stl" in words or any(w in s for w in ["saint louis", "st louis", "blues"]): return "STL"
    if "vgk" in words or any(w in s for w in ["vegas", "golden knight", "knights"]): return "VGK"
    if "nyr" in words or any(w in s for w in ["ranger", "rangers"]): return "NYR"
    if "mtl" in words or any(w in s for w in ["canadien", "canadiens", "montreal"]): return "MTL"
    if "dal" in words or any(w in s for w in ["dallas", "star", "stars"]): return "DAL"
    if "buf" in words or any(w in s for w in ["buffalo", "sabre", "sabres"]): return "BUF"
    if "phi" in words or any(w in s for w in ["philadelph", "flyer", "flyers"]): return "PHI"
    if "sea" in words or any(w in s for w in ["seattle", "kraken"]): return "SEA"
    return None

def extract_owner_abbr(cell_val):
    if pd.isna(cell_val): return None
    s = str(cell_val).strip()
    if not s or s.upper() in ['NAN', 'NONE', '']: return None
    first_part = re.split(r'[\(\/\-\s]', s)[0].strip()
    abbr = to_team_abbr(first_part)
    if abbr: return abbr
    return to_team_abbr(s)

def get_nhl_team_branding(team_name):
    name_lower = str(team_name).lower()
    mapping = [
        ("anaheim", "https://assets.nhle.com/logos/nhl/svg/ANA_light.svg", "#F47A38"),
        ("boston", "https://assets.nhle.com/logos/nhl/svg/BOS_light.svg", "#FFB81C"),
        ("buffalo", "https://assets.nhle.com/logos/nhl/svg/BUF_light.svg", "#FACC15"),
        ("calgary", "https://assets.nhle.com/logos/nhl/svg/CGY_light.svg", "#C8102E"),
        ("carolina", "https://assets.nhle.com/logos/nhl/svg/CAR_light.svg", "#CC0000"),
        ("chicago", "https://assets.nhle.com/logos/nhl/svg/CHI_light.svg", "#CF0A2C"),
        ("colorado", "https://assets.nhle.com/logos/nhl/svg/COL_light.svg", "#6F263D"),
        ("columbus", "https://assets.nhle.com/logos/nhl/svg/CBJ_light.svg", "#002654"),
        ("dallas", "https://assets.nhle.com/logos/nhl/svg/DAL_light.svg", "#006847"),
        ("detroit", "https://assets.nhle.com/logos/nhl/svg/DET_light.svg", "#CE1126"),
        ("edmonton", "https://assets.nhle.com/logos/nhl/svg/EDM_light.svg", "#FF4C00"),
        ("florida", "https://assets.nhle.com/logos/nhl/svg/FLA_light.svg", "#C8102E"),
        ("kings", "https://assets.nhle.com/logos/nhl/svg/LAK_light.svg", "#A2AAAD"),
        ("minnesota", "https://assets.nhle.com/logos/nhl/svg/MIN_light.svg", "#154734"),
        ("montr", "https://assets.nhle.com/logos/nhl/svg/MTL_light.svg", "#AF1E2D"),
        ("nashville", "https://assets.nhle.com/logos/nhl/svg/NSH_light.svg", "#FFB81C"),
        ("jersey", "https://assets.nhle.com/logos/nhl/svg/NJD_light.svg", "#CE1126"),
        ("islander", "https://assets.nhle.com/logos/nhl/svg/NYI_light.svg", "#F47D30"),
        ("ranger", "https://assets.nhle.com/logos/nhl/svg/NYR_light.svg", "#38BDF8"),
        ("ottawa", "https://assets.nhle.com/logos/nhl/svg/OTT_light.svg", "#DA1A32"),
        ("philadelphia", "https://assets.nhle.com/logos/nhl/svg/PHI_light.svg", "#F74902"),
        ("pittsburgh", "https://assets.nhle.com/logos/nhl/svg/PIT_light.svg", "#FCB514"),
        ("san jose", "https://assets.nhle.com/logos/nhl/svg/SJS_light.svg", "#006D75"),
        ("seattle", "https://assets.nhle.com/logos/nhl/svg/SEA_light.svg", "#99D9D9"),
        ("louis", "https://assets.nhle.com/logos/nhl/svg/STL_light.svg", "#38BDF8"),
        ("tampa", "https://assets.nhle.com/logos/nhl/svg/TBL_light.svg", "#002868"),
        ("toronto", "https://assets.nhle.com/logos/nhl/svg/TOR_light.svg", "#00205B"),
        ("utah", "https://assets.nhle.com/logos/nhl/svg/UTA_light.svg", "#71AFE5"),
        ("vancouver", "https://assets.nhle.com/logos/nhl/svg/VAN_light.svg", "#00843D"),
        ("vegas", "https://assets.nhle.com/logos/nhl/svg/VGK_light.svg", "#B4975A"),
        ("washington", "https://assets.nhle.com/logos/nhl/svg/WSH_light.svg", "#C8102E"),
        ("winnipeg", "https://assets.nhle.com/logos/nhl/svg/WPG_light.svg", "#041E42")
    ]
    for key, logo, color in mapping:
        if key in name_lower: return logo, color
    return "https://assets.nhle.com/logos/nhl/svg/NHL_light.svg", "#94A3B8"

# =========================================================
# 4. GESTION DES FICHIERS & ÉTAT PERMANENT (+ SYNCHRO GITHUB)
# =========================================================
DATA_FILE = "phdm_state.json"

ROSTER_RULES = {
    "att": {"label": "Attaquants", "min": 19, "max": 25, "max_tot": 80},
    "def": {"label": "Défenseurs", "min": 7, "max": 11, "max_tot": 30},
    "gard": {"label": "Gardiens", "min": 2, "max": 4, "max_tot": 18}
}

def get_file_path(filename):
    for c in [os.path.join(os.path.dirname(__file__), filename), filename, os.path.join("pages", filename)]:
        if os.path.exists(c): return c
    return filename

def match_team_name(val):
    abbr = to_team_abbr(val)
    if abbr and abbr in ABBR_TO_TEAM:
        return ABBR_TO_TEAM[abbr]
    return None

def find_sheet_by_keyword(path, keywords):
    try:
        xls = pd.ExcelFile(path)
        for s in xls.sheet_names:
            s_clean = clean_str(s)
            if any(k in s_clean for k in keywords):
                return s
    except Exception:
        pass
    return None

def init_rosters_from_csv():
    path = get_file_path("alignements_phdm.csv")
    if not os.path.exists(path): return False
    try: df_raw = pd.read_csv(path, sep=None, engine='python', encoding='utf-8-sig')
    except: df_raw = pd.read_csv(path, sep=';', encoding='latin1')

    col_map = {clean_str(c): c for c in df_raw.columns}
    col_equipe = col_map.get("equipe pool") or col_map.get("equipe") or df_raw.columns[0]
    col_statut = col_map.get("statut") or df_raw.columns[1]
    col_pos = col_map.get("position") or col_map.get("pos") or df_raw.columns[2]
    col_joueur = col_map.get("joueurs") or col_map.get("joueur") or df_raw.columns[3]
    col_team = col_map.get("t") or col_map.get("equipe lnh") or df_raw.columns[4]
    col_age = col_map.get("a") or col_map.get("age") or df_raw.columns[5]
    col_exp = col_map.get("exp") or df_raw.columns[6]

    df_raw["Standard_Team"] = df_raw[col_equipe].apply(match_team_name)

    for team_name in POOLERS.keys():
        slug = SLUG_MAP[team_name]
        df_team = df_raw[df_raw["Standard_Team"] == team_name]

        for role_code, pos_cat, is_partant in [
            ("att", "ATT", True), ("banc_att", "ATT", False),
            ("def", "DEF", True), ("banc_def", "DEF", False),
            ("gard", "GARD", True), ("banc_gard", "GARD", False)
        ]:
            statut_filter = "part" if is_partant else "banc"
            sub = df_team[(df_team[col_statut].astype(str).str.strip().str.lower().str.contains(statut_filter)) & (df_team[col_pos].astype(str).str.strip().str.upper().str.startswith(pos_cat[:3]))]

            rows = []
            for _, r in sub.iterrows():
                nom = str(r[col_joueur]).strip()
                t_init = str(r[col_team]).strip() if pd.notna(r[col_team]) else ""
                try: age_csv = int(r[col_age])
                except: age_csv = 0
                try: exp = int(r[col_exp])
                except: exp = 0

                t_reel, age_reel, pj, pts = get_player_live_stats(nom, t_init, age_csv, pos_cat)
                rows.append([0, nom, t_reel, age_reel, exp, pj, pts])

            df_res = pd.DataFrame(rows, columns=["No", "Joueurs", "T", "A", "EXP", "PJ", "Pts"]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
            df_res["No"] = range(1, len(df_res) + 1)
            st.session_state[f"{slug}_df_{role_code}"] = df_res

        st.session_state[f"{slug}_init"] = True
    return True

def init_recrues_from_csv():
    path = get_file_path("recrues_phdm.csv")
    cols_master = ["No", "Joueurs", "Pos", "Année", "Ronde", "Rang", "Cotes", "Club Junior", "Ligue", "Équipe LNH (Réelle)", "Équipe PHDM"]

    if not os.path.exists(path):
        st.session_state["df_all_recrues"] = pd.DataFrame(columns=cols_master)
        for slug in SLUG_MAP.values(): st.session_state[f"{slug}_df_recrues"] = pd.DataFrame(columns=cols_master + ["Pts"])
        return False

    try: df_raw = pd.read_csv(path, sep=None, engine='python', encoding='utf-8-sig', header=None)
    except: df_raw = pd.read_csv(path, sep=';', encoding='latin1', header=None)

    rows_master = []
    current_nhl_team = "Libre / Non repêché"

    for i, r in df_raw.iterrows():
        val_a = str(r.iloc[0]).strip() if len(r) > 0 and pd.notna(r.iloc[0]) else ""
        val_b = str(r.iloc[1]).strip() if len(r) > 1 and pd.notna(r.iloc[1]) else ""

        if val_a == "" and val_b == "": continue
        if val_a.lower() in ["no", "num", "numéro", "numero"] or val_b.lower() in ["joueurs", "joueur", "nom", "noms"]: continue

        if val_a.isdigit() and val_b != "" and val_b.lower() != "nan":
            nom = val_b
            pos = str(r.iloc[2]).strip() if len(r) > 2 and pd.notna(r.iloc[2]) else ""
            annee = str(r.iloc[3]).strip() if len(r) > 3 and pd.notna(r.iloc[3]) else ""
            ronde = str(r.iloc[4]).strip() if len(r) > 4 and pd.notna(r.iloc[4]) else ""
            if len(r) > 5 and pd.notna(r.iloc[5]):
                rang = str(r.iloc[5]).strip()
            else:
                rang = ""

            c1 = str(r.iloc[6]).strip() if len(r) > 6 and pd.notna(r.iloc[6]) else ""
            c2 = str(r.iloc[7]).strip() if len(r) > 7 and pd.notna(r.iloc[7]) else ""
            c3 = str(r.iloc[8]).strip() if len(r) > 8 and pd.notna(r.iloc[8]) else ""
            cotes = f"{c1}-{c2}-{c3}".strip('-')
            if cotes == "--": cotes = ""

            club = str(r.iloc[9]).strip() if len(r) > 9 and pd.notna(r.iloc[9]) else ""
            ligue = str(r.iloc[10]).strip() if len(r) > 10 and pd.notna(r.iloc[10]) else ""

            proprio = ""
            if len(r) > 16 and pd.notna(r.iloc[16]):
                proprio = str(r.iloc[16]).strip()
            elif len(r) > 14 and pd.notna(r.iloc[14]):
                proprio = str(r.iloc[14]).strip()

            if proprio.lower() in ["nan", "none"]: proprio = ""

            rows_master.append([len(rows_master)+1, nom, pos, annee, ronde, rang, cotes, club, ligue, current_nhl_team, proprio])

        elif not val_a.isdigit() and val_a.lower() not in ["nan", ""]:
            if "ronde" not in val_a.lower():
                if "canadien" in val_a.lower() and "montr" in val_a.lower():
                    current_nhl_team = "Montréal Canadiens"
                elif "draft" in val_a.lower() or "repêchage" in val_a.lower():
                    current_nhl_team = "Draft NHL"
                else:
                    current_nhl_team = val_a

    df_master = pd.DataFrame(rows_master, columns=cols_master)
    df_master["Standard_PHDM"] = df_master["Équipe PHDM"].apply(match_team_name)
    st.session_state["df_all_recrues"] = df_master

    for team_name in POOLERS.keys():
        slug = SLUG_MAP[team_name]
        sub = df_master[df_master["Standard_PHDM"] == team_name].copy()
        sub["Pts"] = 0
        sub = sub.sort_values(by="Joueurs").reset_index(drop=True)
        sub["No"] = range(1, len(sub) + 1)
        st.session_state[f"{slug}_df_recrues"] = sub

    return True

def init_drafts_from_excel():
    abbrs = list(TEAM_ABBR_MAP.values())
    for annee in DRAFT_YEARS:
        st.session_state[f"df_draft_{annee}"] = pd.DataFrame(
            {t: [t]*15 for t in abbrs},
            index=[f"Ronde {i}" for i in range(1, 16)]
        )

    if "df_draft_2026" in st.session_state:
        del st.session_state["df_draft_2026"]

    path_excel = get_file_path("gestion_phdm.xlsx")
    if not os.path.exists(path_excel):
        return False

    try:
        xls = pd.ExcelFile(path_excel)
        draft_sheets = [s for s in xls.sheet_names if "repechage" in clean_str(s) or "draft" in clean_str(s)]
        for sheet_name in draft_sheets:
            m_yr = re.search(r'(2027|2028)', str(sheet_name))
            if not m_yr: continue
            yr = m_yr.group(1)

            df_raw = pd.read_excel(path_excel, sheet_name=sheet_name, header=None)
            header_idx = 0
            for idx, row in df_raw.head(10).iterrows():
                row_vals = [str(x).upper().strip() for x in row.values]
                if sum(1 for tm in row_vals if to_team_abbr(tm) is not None) >= 3 or any("RONDE" in v for v in row_vals):
                    header_idx = idx
                    break

            new_cols = []
            for i, val in enumerate(df_raw.iloc[header_idx]):
                val_str = str(val).strip()
                abbr = to_team_abbr(val_str)
                if i == 0 and (abbr is None or "RONDE" in val_str.upper() or val_str.isdigit()):
                    new_cols.append("Ronde")
                elif abbr:
                    new_cols.append(abbr)
                else:
                    new_cols.append(f"Col_{i}")

            df_sheet = df_raw.iloc[header_idx+1:].copy()
            df_sheet.columns = new_cols
            df_sheet = df_sheet.dropna(how='all').reset_index(drop=True)

            df_clean_draft = st.session_state[f"df_draft_{yr}"].copy()
            for idx_r, row_d in df_sheet.iterrows():
                m_r = re.search(r'\d+', str(row_d.get("Ronde", "")))
                rd_num = int(m_r.group(0)) if m_r else (idx_r + 1)
                rd_label = f"Ronde {rd_num}"
                if rd_label not in df_clean_draft.index:
                    df_clean_draft.loc[rd_label] = {t: t for t in abbrs}

                for orig_abbr in abbrs:
                    if orig_abbr in df_sheet.columns:
                        owner = extract_owner_abbr(row_d[orig_abbr])
                        if owner and owner in abbrs:
                            df_clean_draft.loc[rd_label, orig_abbr] = owner

            st.session_state[f"df_draft_{yr}"] = df_clean_draft
        return True
    except Exception:
        return False

def sync_state_to_github(state_data):
    """Envoie phdm_state.json sur GitHub si les Secrets Streamlit sont configurés."""
    try:
        token = st.secrets.get("GITHUB_TOKEN", "")
        repo = st.secrets.get("GITHUB_REPO", "")
        branch = st.secrets.get("GITHUB_BRANCH", "main")
    except Exception:
        return False, "Secrets non configurés"

    if not token or not repo:
        return False, "Token ou Repo manquant"

    api_url = f"https://api.github.com/repos/{repo}/contents/{DATA_FILE}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "PHDM-Streamlit-App"
    }
    try:
        sha = None
        r_get = requests.get(f"{api_url}?ref={branch}", headers=headers, timeout=10)
        if r_get.status_code == 200:
            sha = r_get.json().get("sha")

        content_str = json.dumps(state_data, ensure_ascii=False, indent=4)
        content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

        payload = {
            "message": "🔄 Sauvegarde automatique PHDM (Streamlit)",
            "content": content_b64,
            "branch": branch
        }
        if sha:
            payload["sha"] = sha

        r_put = requests.put(api_url, headers=headers, json=payload, timeout=15)
        if r_put.status_code in (200, 201):
            return True, "Sauvegardé sur GitHub"
        return False, f"Erreur GitHub ({r_put.status_code})"
    except Exception as e:
        return False, str(e)

def save_state(official_api_updated=False, push_github=False):
    if official_api_updated:
        st.session_state["last_official_update_et"] = datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d %H:%M")

    state_data = {
        "schema_version": 3,
        "last_official_update_et": st.session_state.get("last_official_update_et", ""),
        "rosters": {},
        "drafts": {},
        "all_recrues": [],
        "app_trade_history": st.session_state.get("app_trade_history", []),
        "last_trade_backup": st.session_state.get("last_trade_backup", None)
    }
    for slug in SLUG_MAP.values():
        state_data["rosters"][slug] = {}
        for role in ["att", "def", "gard", "banc_att", "banc_def", "banc_gard", "recrues"]:
            if f"{slug}_df_{role}" in st.session_state:
                state_data["rosters"][slug][role] = st.session_state[f"{slug}_df_{role}"].to_dict(orient="records")
    for annee in DRAFT_YEARS:
        if f"df_draft_{annee}" in st.session_state:
            state_data["drafts"][annee] = st.session_state[f"df_draft_{annee}"].to_dict(orient="index")
    if "df_all_recrues" in st.session_state and not st.session_state["df_all_recrues"].empty:
        state_data["all_recrues"] = st.session_state["df_all_recrues"].to_dict(orient="records")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=4)

    if push_github:
        ok, msg = sync_state_to_github(state_data)
        if ok:
            st.toast("☁️ Sauvegardé automatiquement sur GitHub !")
        elif msg not in ("Secrets non configurés", "Token ou Repo manquant"):
            st.toast(f"⚠️ GitHub : {msg}")

def load_state():
    if not os.path.exists(DATA_FILE): return False
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            state_data = json.load(f)

        for slug, roles in state_data.get("rosters", {}).items():
            for role, records in roles.items():
                df_loaded = pd.DataFrame(records)
                if role != "recrues" and df_loaded.empty:
                    df_loaded = pd.DataFrame(columns=["No", "Joueurs", "T", "A", "EXP", "PJ", "Pts"])
                st.session_state[f"{slug}_df_{role}"] = df_loaded
            st.session_state[f"{slug}_init"] = True

        if state_data.get("schema_version", 1) < 3:
            init_drafts_from_excel()
        else:
            for annee in DRAFT_YEARS:
                if annee in state_data.get("drafts", {}):
                    st.session_state[f"df_draft_{annee}"] = pd.DataFrame.from_dict(state_data["drafts"][annee], orient="index")
                else:
                    abbrs = list(TEAM_ABBR_MAP.values())
                    st.session_state[f"df_draft_{annee}"] = pd.DataFrame({t: [t]*15 for t in abbrs}, index=[f"Ronde {i}" for i in range(1, 16)])

        if "df_draft_2026" in st.session_state:
            del st.session_state["df_draft_2026"]

        if state_data.get("all_recrues"):
            st.session_state["df_all_recrues"] = pd.DataFrame(state_data["all_recrues"])

        st.session_state["app_trade_history"] = state_data.get("app_trade_history", [])
        st.session_state["last_trade_backup"] = state_data.get("last_trade_backup", None)
        st.session_state["last_official_update_et"] = state_data.get("last_official_update_et", "")
        return True
    except Exception:
        return False

def enforce_roster_limits(slug):
    for pos, max_partants in [("att", 25), ("def", 11), ("gard", 4)]:
        df_p = st.session_state.get(f"{slug}_df_{pos}")
        df_b = st.session_state.get(f"{slug}_df_banc_{pos}")
        if df_p is not None and df_b is not None and len(df_p) > max_partants:
            df_p = df_p.sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
            demoted = df_p.iloc[max_partants:].copy()
            df_p = df_p.iloc[:max_partants].copy()
            df_b = pd.concat([df_b, demoted], ignore_index=True).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
            df_p["No"] = range(1, len(df_p) + 1)
            df_b["No"] = range(1, len(df_b) + 1)
            st.session_state[f"{slug}_df_{pos}"] = df_p
            st.session_state[f"{slug}_df_banc_{pos}"] = df_b

# =========================================================
# 4B. MOTEUR D'ÉCHANGES DIRECTS (RÈGLES STRICTES DE PLANCHER)
# =========================================================
def get_team_trade_assets(team_name):
    slug = SLUG_MAP[team_name]
    abbr = TEAM_ABBR_MAP[team_name]
    players = []

    role_labels = [
        ("att", "att", True, "ATT - Partant"),
        ("banc_att", "att", False, "ATT - Banc"),
        ("def", "def", True, "DEF - Partant"),
        ("banc_def", "def", False, "DEF - Banc"),
        ("gard", "gard", True, "GARD - Partant"),
        ("banc_gard", "gard", False, "GARD - Banc"),
        ("recrues", "recrues", False, "Recrue")
    ]

    for role_key, pos_group, is_partant, badge in role_labels:
        df_r = st.session_state.get(f"{slug}_df_{role_key}", pd.DataFrame())
        if df_r.empty: continue
        for idx, row in df_r.iterrows():
            nom = str(row.get("Joueurs", "")).strip()
            if not nom or nom == "[Carreau Libre]": continue
            if role_key == "recrues":
                lnh = str(row.get("Équipe LNH (Réelle)", "")).strip()
                pos_r = str(row.get("Pos", "")).strip()
                label = f"[{badge}] {nom} ({pos_r} - {lnh})"
            else:
                pts = int(row.get("Pts", 0)) if pd.notna(row.get("Pts", 0)) else 0
                t_lnh = str(row.get("T", "")).strip()
                label = f"[{badge}] {nom} ({t_lnh} · {pts} pts)"
            players.append({
                "label": label,
                "name": nom,
                "role_key": role_key,
                "pos_group": pos_group,
                "is_partant": is_partant
            })

    picks = []
    for yr in DRAFT_YEARS:
        df_d = st.session_state.get(f"df_draft_{yr}")
        if df_d is None or df_d.empty: continue
        for rd_label in df_d.index:
            for orig_abbr in TEAM_ABBR_MAP.values():
                if orig_abbr in df_d.columns:
                    owner = str(df_d.loc[rd_label, orig_abbr]).strip().upper()
                    if owner == abbr:
                        tag = "Choix d'origine" if orig_abbr == abbr else f"Acquis de {orig_abbr}"
                        label = f"{yr} · {rd_label} ({tag} : {orig_abbr})"
                        picks.append({
                            "label": label,
                            "year": yr,
                            "round": rd_label,
                            "orig_abbr": orig_abbr
                        })

    return players, picks

def clone_team_rosters(slug):
    res = {}
    for r in ["att", "banc_att", "def", "banc_def", "gard", "banc_gard", "recrues"]:
        df_cur = st.session_state.get(f"{slug}_df_{r}", pd.DataFrame())
        res[r] = df_cur.copy()
    return res

def simulate_trade(team_a, team_b, out_a, out_b, picks_a, picks_b):
    slug_a, slug_b = SLUG_MAP[team_a], SLUG_MAP[team_b]
    abbr_a, abbr_b = TEAM_ABBR_MAP[team_a], TEAM_ABBR_MAP[team_b]

    ros_a = clone_team_rosters(slug_a)
    ros_b = clone_team_rosters(slug_b)

    def extract_players(ros_src, assets):
        extracted = []
        for asset in assets:
            rk = asset["role_key"]
            df_src = ros_src[rk]
            mask = df_src["Joueurs"].astype(str).str.strip() == asset["name"]
            if mask.any():
                row_df = df_src[mask].iloc[[0]].copy()
                ros_src[rk] = df_src[~mask].reset_index(drop=True)
                extracted.append((asset, row_df))
        return extracted

    moved_from_a = extract_players(ros_a, out_a)
    moved_from_b = extract_players(ros_b, out_b)

    def inject_players(ros_dest, dest_team_name, incoming_list):
        notes = []
        for asset, row_df in incoming_list:
            pos_g = asset["pos_group"]
            if pos_g == "recrues":
                row_df["Équipe PHDM"] = dest_team_name
                if "Standard_PHDM" in row_df.columns:
                    row_df["Standard_PHDM"] = dest_team_name
                ros_dest["recrues"] = pd.concat([ros_dest["recrues"], row_df], ignore_index=True)
                notes.append(f"🌱 {asset['name']} ajouté aux recrues de {TEAM_ABBR_MAP[dest_team_name]}.")
            else:
                if asset["is_partant"]:
                    target_key = pos_g
                    notes.append(f"⚡ {asset['name']} s'ajoute aux PARTANTS ({ROSTER_RULES[pos_g]['label']}) de {TEAM_ABBR_MAP[dest_team_name]}.")
                else:
                    target_key = f"banc_{pos_g}"
                    notes.append(f"📋 {asset['name']} arrive sur le banc ({ROSTER_RULES[pos_g]['label']}) de {TEAM_ABBR_MAP[dest_team_name]}.")

                ros_dest[target_key] = pd.concat([ros_dest[target_key], row_df], ignore_index=True)
                ros_dest[target_key] = ros_dest[target_key].sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
        return notes

    notes_a = inject_players(ros_b, team_b, moved_from_a)
    notes_b = inject_players(ros_a, team_a, moved_from_b)
    all_notes = notes_a + notes_b

    for r_k in ["att", "banc_att", "def", "banc_def", "gard", "banc_gard"]:
        if not ros_a[r_k].empty: ros_a[r_k]["No"] = range(1, len(ros_a[r_k]) + 1)
        if not ros_b[r_k].empty: ros_b[r_k]["No"] = range(1, len(ros_b[r_k]) + 1)
    if not ros_a["recrues"].empty:
        ros_a["recrues"] = ros_a["recrues"].sort_values(by="Joueurs").reset_index(drop=True)
        ros_a["recrues"]["No"] = range(1, len(ros_a["recrues"]) + 1)
    if not ros_b["recrues"].empty:
        ros_b["recrues"] = ros_b["recrues"].sort_values(by="Joueurs").reset_index(drop=True)
        ros_b["recrues"]["No"] = range(1, len(ros_b["recrues"]) + 1)

    def check_quotas_and_limits(ros, t_name):
        errors = []
        warnings = []
        stats = {}
        tot_conc = 0
        for pos, rules in ROSTER_RULES.items():
            n_p = len(ros[pos])
            n_b = len(ros[f"banc_{pos}"])
            n_tot = n_p + n_b
            tot_conc += n_tot
            stats[pos] = (n_p, n_tot)

            if n_p < rules["min"]:
                needed = rules["min"] - n_p
                errors.append(
                    f"❌ TRANSACTION BLOQUÉE : {t_name} n'aurait plus que {n_p} {rules['label'].lower()} partant(s) "
                    f"(minimum légal strict : {rules['min']}). L'équipe doit obligatoirement recevoir au moins {needed} "
                    f"{rules['label'].lower()} partant(s) dans la transaction pour combler le départ !"
                )
            elif n_p > rules["max"]:
                surplus = n_p - rules["max"]
                errors.append(
                    f"❌ TRANSACTION BLOQUÉE : {t_name} aurait {n_p} {rules['label'].lower()} partants "
                    f"(maximum légal : {rules['max']}). Excédent de {surplus} partant(s) !"
                )

            if n_tot > rules["max_tot"]:
                warnings.append(f"⚠️ {t_name} ({rules['label']}) : {n_tot} joueurs au total (max permis : {rules['max_tot']}).")

        stats["total"] = tot_conc
        if tot_conc > 128:
            warnings.append(f"⚠️ {t_name} : {tot_conc} joueurs dans la concession (max permis : 128).")
        return errors, warnings, stats

    err_a, warn_a, stats_a = check_quotas_and_limits(ros_a, team_a)
    err_b, warn_b, stats_b = check_quotas_and_limits(ros_b, team_b)

    drafts_sim = {}
    for yr in DRAFT_YEARS:
        if f"df_draft_{yr}" in st.session_state:
            drafts_sim[yr] = st.session_state[f"df_draft_{yr}"].copy()

    for pk in picks_a:
        if pk["year"] in drafts_sim:
            drafts_sim[pk["year"]].loc[pk["round"], pk["orig_abbr"]] = abbr_b
    for pk in picks_b:
        if pk["year"] in drafts_sim:
            drafts_sim[pk["year"]].loc[pk["round"], pk["orig_abbr"]] = abbr_a

    return {
        "ros_a": ros_a, "ros_b": ros_b,
        "drafts_sim": drafts_sim,
        "notes": all_notes,
        "errors": err_a + err_b,
        "warnings": warn_a + warn_b,
        "stats_a": stats_a, "stats_b": stats_b
    }

def execute_trade(team_a, team_b, out_a, out_b, picks_a, picks_b, sim_result):
    slug_a, slug_b = SLUG_MAP[team_a], SLUG_MAP[team_b]

    backup = {
        "team_a": team_a, "team_b": team_b,
        "rosters": {
            slug_a: {r: st.session_state[f"{slug_a}_df_{r}"].to_dict(orient="records") for r in ["att", "banc_att", "def", "banc_def", "gard", "banc_gard", "recrues"]},
            slug_b: {r: st.session_state[f"{slug_b}_df_{r}"].to_dict(orient="records") for r in ["att", "banc_att", "def", "banc_def", "gard", "banc_gard", "recrues"]}
        },
        "drafts": {yr: st.session_state[f"df_draft_{yr}"].to_dict(orient="index") for yr in DRAFT_YEARS if f"df_draft_{yr}" in st.session_state},
        "all_recrues": st.session_state["df_all_recrues"].to_dict(orient="records") if "df_all_recrues" in st.session_state else [],
        "app_trade_history": list(st.session_state.get("app_trade_history", []))
    }
    st.session_state["last_trade_backup"] = backup

    for r, df_new in sim_result["ros_a"].items():
        st.session_state[f"{slug_a}_df_{r}"] = df_new
    for r, df_new in sim_result["ros_b"].items():
        st.session_state[f"{slug_b}_df_{r}"] = df_new

    if "df_all_recrues" in st.session_state and not st.session_state["df_all_recrues"].empty:
        df_rec_master = st.session_state["df_all_recrues"].copy()
        for asset in out_a:
            if asset["pos_group"] == "recrues":
                mask = df_rec_master["Joueurs"].apply(clean_str) == clean_str(asset["name"])
                df_rec_master.loc[mask, "Équipe PHDM"] = team_b
                if "Standard_PHDM" in df_rec_master.columns:
                    df_rec_master.loc[mask, "Standard_PHDM"] = team_b
        for asset in out_b:
            if asset["pos_group"] == "recrues":
                mask = df_rec_master["Joueurs"].apply(clean_str) == clean_str(asset["name"])
                df_rec_master.loc[mask, "Équipe PHDM"] = team_a
                if "Standard_PHDM" in df_rec_master.columns:
                    df_rec_master.loc[mask, "Standard_PHDM"] = team_a
        st.session_state["df_all_recrues"] = df_rec_master

    for yr, df_d in sim_result["drafts_sim"].items():
        st.session_state[f"df_draft_{yr}"] = df_d

    a_items = [a["name"] for a in out_a] + [f"{p['year']} {p['round']} ({p['orig_abbr']})" for p in picks_a]
    b_items = [b["name"] for b in out_b] + [f"{p['year']} {p['round']} ({p['orig_abbr']})" for p in picks_b]

    if "app_trade_history" not in st.session_state:
        st.session_state["app_trade_history"] = []

    st.session_state["app_trade_history"].insert(0, {
        "Date": format_date_fr(datetime.now()),
        "Équipe A": team_a,
        "A cédé": ", ".join(a_items) if a_items else "Rien",
        "Équipe B": team_b,
        "B a cédé": ", ".join(b_items) if b_items else "Rien"
    })

    save_state(push_github=True)

def undo_last_trade():
    backup = st.session_state.get("last_trade_backup")
    if not backup: return False
    for slug, roles in backup.get("rosters", {}).items():
        for role, records in roles.items():
            df_b = pd.DataFrame(records)
            if role != "recrues" and df_b.empty:
                df_b = pd.DataFrame(columns=["No", "Joueurs", "T", "A", "EXP", "PJ", "Pts"])
            st.session_state[f"{slug}_df_{role}"] = df_b
    for yr, records in backup.get("drafts", {}).items():
        st.session_state[f"df_draft_{yr}"] = pd.DataFrame.from_dict(records, orient="index")
    if backup.get("all_recrues"):
        st.session_state["df_all_recrues"] = pd.DataFrame(backup["all_recrues"])
    st.session_state["app_trade_history"] = backup.get("app_trade_history", [])
    st.session_state["last_trade_backup"] = None
    save_state(push_github=True)
    return True

# =========================================================
# INITIALISATION DES DONNÉES
# =========================================================
data_loaded = load_state()
if not data_loaded or (f"{SLUG_MAP['Dallas Stars']}_df_att" not in st.session_state or st.session_state[f"{SLUG_MAP['Dallas Stars']}_df_att"].empty):
    init_rosters_from_csv()
    init_recrues_from_csv()
    init_drafts_from_excel()
    if "app_trade_history" not in st.session_state:
        st.session_state["app_trade_history"] = []
    save_state(official_api_updated=True)

if "df_all_recrues" not in st.session_state: init_recrues_from_csv()
for yr in DRAFT_YEARS:
    if f"df_draft_{yr}" not in st.session_state:
        init_drafts_from_excel()
        break
for slug in SLUG_MAP.values(): enforce_roster_limits(slug)
def _find_1_to_1_swaps_global(df_act, df_banc):
    k = 0
    for i in range(min(len(df_act), len(df_banc))):
        if df_banc.iloc[i]["Pts"] > df_act.iloc[len(df_act) - 1 - i]["Pts"]:
            k += 1
        else:
            break
    if k == 0:
        return [], []
    cutoff_pts = df_banc.iloc[k - 1]["Pts"]
    cands = df_banc[df_banc["Pts"] >= cutoff_pts]["Joueurs"].tolist()
    return df_act.iloc[len(df_act) - k:]["Joueurs"].tolist(), cands[:k]

if is_admin:
    with st.sidebar:
        st.divider()
        if st.button("⚡ PERMUTATIONS (1 POUR 1) — TOUTES LES ÉQUIPES", use_container_width=True):
            total_swaps = 0
            equipes_touchees = []
            for t_name, slg in SLUG_MAP.items():
                swaps_team = 0
                for pos_act, pos_banc in [("att", "banc_att"), ("def", "banc_def"), ("gard", "banc_gard")]:
                    ka, kb = f"{slg}_df_{pos_act}", f"{slg}_df_{pos_banc}"
                    a_df = st.session_state.get(ka, pd.DataFrame())
                    b_df = st.session_state.get(kb, pd.DataFrame())
                    if a_df.empty or b_df.empty:
                        continue
                    a_df = a_df.sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                    b_df = b_df.sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                    rl, gl = _find_1_to_1_swaps_global(a_df, b_df)
                    if rl and gl:
                        new_a = pd.concat([a_df[~a_df["Joueurs"].isin(rl)], b_df[b_df["Joueurs"].isin(gl)]]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_b = pd.concat([b_df[~b_df["Joueurs"].isin(gl)], a_df[a_df["Joueurs"].isin(rl)]]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_a["No"] = range(1, len(new_a) + 1)
                        new_b["No"] = range(1, len(new_b) + 1)
                        st.session_state[ka] = new_a
                        st.session_state[kb] = new_b
                        swaps_team += len(rl)
                if swaps_team > 0:
                    total_swaps += swaps_team
                    equipes_touchees.append(f"{TEAM_ABBR_MAP.get(t_name, t_name)} ({swaps_team})")

            if total_swaps > 0:
                save_state(push_github=True)
                st.session_state["global_perm_feedback"] = ("success", f"✅ {total_swaps} permutation(s) appliquée(s) : {', '.join(equipes_touchees)}")
                st.rerun()
            else:
                st.session_state["global_perm_feedback"] = ("info", "ℹ️ Aucune permutation (1 pour 1) à faire : toutes les équipes sont déjà optimales.")

        if "global_perm_feedback" in st.session_state:
            lvl, msg = st.session_state.pop("global_perm_feedback")
            if lvl == "success":
                st.success(msg)
            else:
                st.info(msg)
# =========================================================
# 5. INITIALISATION DU THÈME DYNAMIQUE DE L'ÉQUIPE
# =========================================================
team_totals = []
for t_name in POOLERS.keys():
    s = SLUG_MAP[t_name]
    pts_att = int(st.session_state.get(f"{s}_df_att", pd.DataFrame([{"Pts": 0}]))["Pts"].sum())
    pts_def = int(st.session_state.get(f"{s}_df_def", pd.DataFrame([{"Pts": 0}]))["Pts"].sum())
    pts_gard = int(st.session_state.get(f"{s}_df_gard", pd.DataFrame([{"Pts": 0}]))["Pts"].sum())
    team_totals.append({"team": t_name, "pooler": POOLERS.get(t_name, ""), "slug": s, "total": pts_att + pts_def + pts_gard, "att": pts_att, "def": pts_def, "gard": pts_gard})

rank_general = sorted(team_totals, key=lambda x: x["total"], reverse=True)
premier_general = rank_general[0]["team"] if rank_general else ""
rank_att = sorted([t for t in team_totals if t["team"] != premier_general], key=lambda x: x["att"], reverse=True)
rank_def = sorted([t for t in team_totals if t["team"] != premier_general], key=lambda x: x["def"], reverse=True)
rank_gard = sorted([t for t in team_totals if t["team"] != premier_general], key=lambda x: x["gard"], reverse=True)

draft_base_order = sorted(team_totals, key=lambda x: (x["total"], x["team"]), reverse=False)

if "current_team" not in st.session_state:
    st.session_state["current_team"] = rank_general[0]["team"] if rank_general else list(POOLERS.keys())[0]
active_team_name = st.session_state["current_team"]
team_slug = SLUG_MAP[active_team_name]

THEMES = {
    "Dallas Stars": {
        "bg_top": "#0F291E", "bg_bottom": "#050B08",
        "primary": "#10B981", "primary_glow": "rgba(16,185,129,0.4)",
        "accent": "#E2E8F0", "btn_grad": "linear-gradient(135deg, #10B981, #047857)",
        "card_bg": "rgba(15, 30, 22, 0.6)"
    },
    "Montréal Canadiens": {
        "bg_top": "#2E0F15", "bg_bottom": "#0B1121",
        "primary": "#EF4444", "primary_glow": "rgba(239,68,68,0.4)",
        "accent": "#60A5FA", "btn_grad": "linear-gradient(135deg, #EF4444, #1E3A8A)",
        "card_bg": "rgba(30, 15, 20, 0.6)"
    },
    "Saint-Louis Blues": {
        "bg_top": "#0F172A", "bg_bottom": "#020617",
        "primary": "#3B82F6", "primary_glow": "rgba(59,130,246,0.4)",
        "accent": "#FACC15", "btn_grad": "linear-gradient(135deg, #3B82F6, #1D4ED8)",
        "card_bg": "rgba(15, 25, 45, 0.6)"
    },
    "Vegas Golden Knights": {
        "bg_top": "#272215", "bg_bottom": "#111315",
        "primary": "#D4AF37", "primary_glow": "rgba(212,175,55,0.4)",
        "accent": "#94A3B8", "btn_grad": "linear-gradient(135deg, #D4AF37, #856014)",
        "card_bg": "rgba(35, 30, 20, 0.6)"
    },
    "Chicago Black Hawks": {
        "bg_top": "#2A0A0A", "bg_bottom": "#0A0A0A",
        "primary": "#EF4444", "primary_glow": "rgba(239,68,68,0.4)",
        "accent": "#F8FAFC", "btn_grad": "linear-gradient(135deg, #EF4444, #991B1B)",
        "card_bg": "rgba(30, 10, 10, 0.6)"
    },
    "Seattle Kraken": {
        "bg_top": "#081E2D", "bg_bottom": "#030B14",
        "primary": "#67E8F9", "primary_glow": "rgba(103,232,249,0.4)",
        "accent": "#F97316", "btn_grad": "linear-gradient(135deg, #06B6D4, #0369A1)",
        "card_bg": "rgba(10, 25, 40, 0.6)"
    },
    "Buffalo Sabres": {
        "bg_top": "#0B1B36", "bg_bottom": "#050A14",
        "primary": "#FACC15", "primary_glow": "rgba(250,204,21,0.4)",
        "accent": "#60A5FA", "btn_grad": "linear-gradient(135deg, #3B82F6, #FACC15)",
        "card_bg": "rgba(15, 25, 45, 0.6)"
    },
    "Philadelphia Flyers": {
        "bg_top": "#2D1608", "bg_bottom": "#0A0502",
        "primary": "#F97316", "primary_glow": "rgba(249,115,22,0.4)",
        "accent": "#F8FAFC", "btn_grad": "linear-gradient(135deg, #F97316, #111111)",
        "card_bg": "rgba(35, 15, 5, 0.6)"
    },
    "UCKA MOCKBA": {
        "bg_top": "#2A0A10", "bg_bottom": "#0A1020",
        "primary": "#E11D48", "primary_glow": "rgba(225,29,72,0.4)",
        "accent": "#FBBF24", "btn_grad": "linear-gradient(135deg, #E11D48, #881337)",
        "card_bg": "rgba(30, 10, 15, 0.6)"
    },
    "New-York Rangers": {
        "bg_top": "#0A1930", "bg_bottom": "#050814",
        "primary": "#3B82F6", "primary_glow": "rgba(59,130,246,0.4)",
        "accent": "#EF4444", "btn_grad": "linear-gradient(135deg, #3B82F6, #1E3A8A)",
        "card_bg": "rgba(10, 20, 40, 0.6)"
    },
    "Vancouver Canucks": {
        "bg_top": "#0A1C2A", "bg_bottom": "#051012",
        "primary": "#10B981", "primary_glow": "rgba(16,185,129,0.4)",
        "accent": "#3B82F6", "btn_grad": "linear-gradient(135deg, #3B82F6, #10B981)",
        "card_bg": "rgba(10, 25, 30, 0.6)"
    }
}

DEFAULT_THEME = {
    "bg_top": "#1E293B", "bg_bottom": "#0B1120",
    "primary": "#38BDF8", "primary_glow": "rgba(56,189,248,0.4)",
    "accent": "#94A3B8", "btn_grad": "linear-gradient(135deg, #0284C7, #0369A1)",
    "card_bg": "rgba(30, 41, 59, 0.5)"
}

t = THEMES.get(active_team_name, DEFAULT_THEME)

st.markdown(f"""
<style>
    .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{
        background: radial-gradient(circle at 50% 0%, {t['bg_top']} 0%, {t['bg_bottom']} 100%) !important;
        color: #E2E8F0 !important;
    }}
    .block-container {{ padding: 3.5rem 2rem 2.5rem 2rem !important; max-width: 100% !important; }}
    .stMarkdown, p, label, h1, h2, h3, h4 {{ color: #F8FAFC !important; }}
    .stTabs [data-baseweb="tab-list"] {{ gap: 15px; margin-bottom: 2rem; border-bottom: 2px solid rgba(255,255,255,0.1) !important; }}
    .stTabs [data-baseweb="tab"] {{ background-color: transparent !important; border-radius: 0 !important; padding: 0.8rem 1rem !important; font-weight: 800 !important; color: #94A3B8 !important; border: none !important; border-bottom: 3px solid transparent !important; font-size: 1.1rem !important; text-transform: uppercase; letter-spacing: 1px; }}
    .stTabs [aria-selected="true"] {{ color: {t['primary']} !important; border-bottom: 3px solid {t['primary']} !important; text-shadow: 0 0 10px {t['primary_glow']}; }}
    .stTabs [aria-selected="true"] p, .stTabs [aria-selected="true"] span {{ color: {t['primary']} !important; }}
    [data-testid="stExpander"] {{ border: 1px solid rgba(255,255,255,0.1) !important; border-radius: 12px !important; margin-bottom: 1rem !important; background: {t['card_bg']} !important; backdrop-filter: blur(10px); overflow: hidden; }}
    [data-testid="stExpander"] summary {{ background-color: rgba(0,0,0,0.4) !important; border-radius: 12px !important; padding: 1rem !important; }}
    [data-testid="stExpander"] summary, [data-testid="stExpander"] summary * {{ color: #E2E8F0 !important; font-weight: 800 !important; font-size: 1.05rem !important; }}
    .stButton > button {{ background: {t['btn_grad']} !important; color: #FFFFFF !important; font-weight: 900 !important; border: 1px solid {t['primary']} !important; border-radius: 8px !important; padding: 0.5rem 1rem !important; text-transform: uppercase; letter-spacing: 0.5px; box-shadow: 0 4px 15px {t['primary_glow']}; transition: all 0.2s ease-in-out; }}
    .stButton > button:hover {{ transform: translateY(-2px); box-shadow: 0 6px 20px {t['primary_glow']}; filter: brightness(1.2); }}
    [data-testid="baseButton-primary"] {{ background: linear-gradient(135deg, #10B981, #059669) !important; border: 1px solid #34D399 !important; box-shadow: 0 4px 15px rgba(16, 185, 129, 0.4); }}
    .stDataFrame {{ border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; background-color: transparent !important; }}
    div[data-baseweb="select"] > div, div[data-baseweb="input"] > div {{ background-color: rgba(0, 0, 0, 0.5) !important; border: 1px solid {t['primary']} !important; color: #F8FAFC !important; }}
</style>
""", unsafe_allow_html=True)

def build_ranking_html(rank_list, score_key, top_n=12):
    html = ""
    for i in range(min(top_n, len(rank_list))):
        team, pts = rank_list[i]['team'], rank_list[i][score_key]
        if i == 0: html += f"<b style='color:#F8FAFC;'>1. {team} : {pts} pts</b><br>"
        else: html += f"<span style='color:#CBD5E1;'>{i+1}. {team} : {pts} pts</span><br>"
    return html

tab_equipes, tab_transactions, tab_reglements, tab_repechage, tab_recrues, tab_direct = st.tabs([
    "🏒 ALIGNEMENTS & ÉQUIPES", "🔁 CENTRE DES TRANSACTIONS", "📜 RÈGLEMENTS DU POOL", "🎟️ CHOIX DE REPÊCHAGE", "🌱 BANQUE DES RECRUES", "🔴 DIRECT (NON OFFICIEL)"
])

# =========================================================
# ONGLET 1 : ÉQUIPES & PRIX (INCHANGÉ VISUELLEMENT)
# =========================================================
with tab_equipes:
    top_gauche, top_droite = st.columns([1.1, 1.9], vertical_alignment="top")
    with top_gauche:
        c_logo, c_title = st.columns([1, 4], vertical_alignment="center")
        with c_logo: st.image(TEAM_LOGOS[active_team_name], width=90)
        with c_title:
            st.markdown(f"<h2 style='margin: 0; font-weight: 900; color: #F8FAFC; font-size: 2.4rem; text-shadow: 0 0 15px rgba(255,255,255,0.3); line-height:1.1;'>{active_team_name.upper()}</h2><div style='font-size: 1.05rem; font-weight: 800; color: {t['primary']}; margin-top: 4px; text-transform: uppercase; letter-spacing: 1px;'>👤 Propriétaire / DG : {POOLERS.get(active_team_name, '')}</div>", unsafe_allow_html=True)

        options_classees = [f"{i+1}. {x['team']} ({x['total']} pts) — {x['pooler']}" for i, x in enumerate(rank_general)]
        mapping_options = {f"{i+1}. {x['team']} ({x['total']} pts) — {x['pooler']}": x['team'] for i, x in enumerate(rank_general)}
        nouveau_choix_label = st.selectbox("🏒 **CHANGER DE CONCESSION :**", options=options_classees, index=options_classees.index([o for o in options_classees if mapping_options[o] == active_team_name][0]))
        if mapping_options[nouveau_choix_label] != active_team_name:
            st.session_state["current_team"] = mapping_options[nouveau_choix_label]
            st.rerun()

    with top_droite:
        st.markdown("<h4 style='margin:0 0 8px 0; text-align:center; color: #94A3B8; font-weight: 800; letter-spacing: 1px;'>💰 PRIX OFFICIELS & CLASSEMENTS (PHDM)</h4>", unsafe_allow_html=True)
        cl1, cl2, cl3, cl4 = st.columns(4)
        card_style = "background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.3);"

        with cl1:
            st.markdown(f"""
            <div style='{card_style} max-height: 300px; overflow-y: auto;'>
                <div style='font-weight: 900; color: #F59E0B; font-size: 0.78rem; border-bottom: 2px solid #F59E0B; padding-bottom: 4px; text-align: center; letter-spacing: 0.5px;'>
                    1. GAGNANT DU POOL
                </div>
                <div style='margin-top: 8px; font-size: 0.76rem; line-height: 1.45;'>
                    {build_ranking_html(rank_general, 'total', len(rank_general))}
                </div>
            </div>
            """, unsafe_allow_html=True)

        with cl2:
            st.markdown(f"""
            <div style='{card_style} max-height: 300px; overflow-y: auto;'>
                <div style='font-weight: 900; color: #EF4444; font-size: 0.78rem; border-bottom: 2px solid #EF4444; padding-bottom: 4px; text-align: center; letter-spacing: 0.5px;'>
                    2. MEILLEURS ATTAQUANTS
                </div>
                <div style='margin-top: 8px; font-size: 0.76rem; line-height: 1.45;'>
                    {build_ranking_html(rank_att, 'att', len(rank_att))}
                    <span style='color: #94A3B8; font-size: 0.68rem;'>❌ 1er Général exclu</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

        with cl3:
            st.markdown(f"""
            <div style='{card_style} max-height: 300px; overflow-y: auto;'>
                <div style='font-weight: 900; color: #38BDF8; font-size: 0.78rem; border-bottom: 2px solid #38BDF8; padding-bottom: 4px; text-align: center; letter-spacing: 0.5px;'>
                    3. MEILLEURS DÉFENSEURS
                </div>
                <div style='margin-top: 8px; font-size: 0.76rem; line-height: 1.45;'>
                    {build_ranking_html(rank_def, 'def', len(rank_def))}
                    <span style='color: #94A3B8; font-size: 0.68rem;'>❌ 1er Général exclu</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

        with cl4:
            st.markdown(f"""
            <div style='{card_style} max-height: 300px; overflow-y: auto;'>
                <div style='font-weight: 900; color: #10B981; font-size: 0.78rem; border-bottom: 2px solid #10B981; padding-bottom: 4px; text-align: center; letter-spacing: 0.5px;'>
                    4. MEILLEURS GARDIENS
                </div>
                <div style='margin-top: 8px; font-size: 0.76rem; line-height: 1.45;'>
                    {build_ranking_html(rank_gard, 'gard', len(rank_gard))}
                    <span style='color: #94A3B8; font-size: 0.68rem;'>❌ 1er Général exclu</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

    st.divider()
    df_att, df_banc_att = st.session_state[f"{team_slug}_df_att"], st.session_state[f"{team_slug}_df_banc_att"]
    df_def, df_banc_def = st.session_state[f"{team_slug}_df_def"], st.session_state[f"{team_slug}_df_banc_def"]
    df_gard, df_banc_gard = st.session_state[f"{team_slug}_df_gard"], st.session_state[f"{team_slug}_df_banc_gard"]

    nb_att_tot = len(df_att) + len(df_banc_att)
    nb_def_tot = len(df_def) + len(df_banc_def)
    nb_gard_tot = len(df_gard) + len(df_banc_gard)
    nb_tot_concession = nb_att_tot + nb_def_tot + nb_gard_tot

    part_att_ok = (19 <= len(df_att) <= 25)
    tot_att_ok = (nb_att_tot <= 80)
    stat_att = part_att_ok and tot_att_ok

    part_def_ok = (7 <= len(df_def) <= 11)
    tot_def_ok = (nb_def_tot <= 30)
    stat_def = part_def_ok and tot_def_ok

    part_gard_ok = (2 <= len(df_gard) <= 4)
    tot_gard_ok = (nb_gard_tot <= 18)
    stat_gard = part_gard_ok and tot_gard_ok

    tot_glob_ok = (nb_tot_concession <= 128)
    stat_globale = tot_glob_ok and stat_att and stat_def and stat_gard

    def get_card_style(is_ok):
        if is_ok:
            return f"background: {t['card_bg']}; backdrop-filter: blur(10px); border: 1px solid rgba(52, 211, 153, 0.3); border-radius: 12px; padding: 12px; text-align: center; box-shadow: inset 0 0 15px rgba(0,0,0,0.5);"
        else:
            return "background: rgba(127, 29, 29, 0.35); backdrop-filter: blur(10px); border: 2px solid #EF4444; border-radius: 12px; padding: 12px; text-align: center; box-shadow: 0 0 18px rgba(239, 68, 68, 0.5);"

    q1, q2, q3, q4 = st.columns(4)
    with q1:
        st.markdown(f"""
        <div style="{get_card_style(stat_att)}">
            <div style="font-size: 0.85rem; font-weight: 900; color:{'#34D399' if stat_att else '#FCA5A5'};">🏒 ATTAQUANTS {'✅' if stat_att else '⚠️'}</div>
            <div style="font-size: 1rem; margin-top: 4px; color:{'#F8FAFC' if part_att_ok else '#EF4444'}; font-weight:{'400' if part_att_ok else '900'};"><b>Partants :</b> {len(df_att)} / [19-25]</div>
            <div style="font-size: 0.85rem; color: {'#94A3B8' if tot_att_ok else '#EF4444'}; font-weight:{'400' if tot_att_ok else '900'};"><b>Total Concession :</b> {nb_att_tot} / 80 max</div>
            <div style="font-size: 0.8rem; font-weight: 800; margin-top: 4px; color: {'#34D399' if stat_att else '#EF4444'}; text-decoration: {'none' if stat_att else 'underline'};">{'✅ Conforme' if stat_att else '⚠️ Quota dépassé'}</div>
        </div>
        """, unsafe_allow_html=True)

    with q2:
        st.markdown(f"""
        <div style="{get_card_style(stat_def)}">
            <div style="font-size: 0.85rem; font-weight: 900; color:{'#34D399' if stat_def else '#FCA5A5'};">🛡️ DÉFENSEURS {'✅' if stat_def else '⚠️'}</div>
            <div style="font-size: 1rem; margin-top: 4px; color:{'#F8FAFC' if part_def_ok else '#EF4444'}; font-weight:{'400' if part_def_ok else '900'};"><b>Partants :</b> {len(df_def)} / [7-11]</div>
            <div style="font-size: 0.85rem; color: {'#94A3B8' if tot_def_ok else '#EF4444'}; font-weight:{'400' if tot_def_ok else '900'};"><b>Total Concession :</b> {nb_def_tot} / 30 max</div>
            <div style="font-size: 0.8rem; font-weight: 800; margin-top: 4px; color: {'#34D399' if stat_def else '#EF4444'}; text-decoration: {'none' if stat_def else 'underline'};">{'✅ Conforme' if stat_def else '⚠️ Quota dépassé'}</div>
        </div>
        """, unsafe_allow_html=True)

    with q3:
        st.markdown(f"""
        <div style="{get_card_style(stat_gard)}">
            <div style="font-size: 0.85rem; font-weight: 900; color:{'#34D399' if stat_gard else '#FCA5A5'};">🥅 GARDIENS {'✅' if stat_gard else '⚠️'}</div>
            <div style="font-size: 1rem; margin-top: 4px; color:{'#F8FAFC' if part_gard_ok else '#EF4444'}; font-weight:{'400' if part_gard_ok else '900'};"><b>Partants :</b> {len(df_gard)} / [2-4]</div>
            <div style="font-size: 0.85rem; color: {'#94A3B8' if tot_gard_ok else '#EF4444'}; font-weight:{'400' if tot_gard_ok else '900'};"><b>Total Concession :</b> {nb_gard_tot} / 18 max</div>
            <div style="font-size: 0.8rem; font-weight: 800; margin-top: 4px; color: {'#34D399' if stat_gard else '#EF4444'}; text-decoration: {'none' if stat_gard else 'underline'};">{'✅ Conforme' if stat_gard else '⚠️ Quota dépassé'}</div>
        </div>
        """, unsafe_allow_html=True)

    with q4:
        st.markdown(f"""
        <div style="{get_card_style(stat_globale)}">
            <div style="font-size: 0.85rem; font-weight: 900; color:{'#34D399' if stat_globale else '#FCA5A5'};">🏢 TOTAL CONCESSION {'✅' if stat_globale else '⚠️'}</div>
            <div style="font-size: 1rem; margin-top: 4px; color:{'#F8FAFC' if tot_glob_ok else '#EF4444'}; font-weight:{'400' if tot_glob_ok else '900'};"><b>Effectif :</b> {nb_tot_concession} / 128 max</div>
            <div style="font-size: 0.85rem; color: #94A3B8;"><b>Statut global :</b></div>
            <div style="font-size: 0.82rem; font-weight: 900; margin-top: 2px; color: {'#34D399' if stat_globale else '#EF4444'}; text-decoration: {'none' if stat_globale else 'underline'};">{'✅ Conforme' if stat_globale else '⚠️ Quota non conforme'}</div>
        </div>
        """, unsafe_allow_html=True)

    def find_1_to_1_swaps(df_act, df_banc):
        k = 0
        for i in range(min(len(df_act), len(df_banc))):
            if df_banc.iloc[i]["Pts"] > df_act.iloc[len(df_act) - 1 - i]["Pts"]: k += 1
            else: break
        if k == 0: return [], []
        cutoff_pts = df_banc.iloc[k - 1]["Pts"]
        cands = df_banc[df_banc["Pts"] >= cutoff_pts]["Joueurs"].tolist()
        return df_act.iloc[len(df_act) - k:]["Joueurs"].tolist(), cands[:k]

    r_att, g_att = find_1_to_1_swaps(df_att, df_banc_att)
    r_def, g_def = find_1_to_1_swaps(df_def, df_banc_def)
    r_gard, g_gard = find_1_to_1_swaps(df_gard, df_banc_gard)

    c1, c2, c3 = st.columns([4, 1.2, 1.2])
    with c1: st.info(f"🟢 **Statut API LNH** : {api_status}")

    with c2:
        if is_admin:
            if st.button("🔄 ACTUALISER API", use_container_width=True):
                st.cache_data.clear()
                nhl_skaters_new, nhl_goalies_new, _ = fetch_nhl_stats()
                globals()["nhl_skaters"] = nhl_skaters_new
                globals()["nhl_goalies"] = nhl_goalies_new
                for slg in SLUG_MAP.values():
                    for role, pos_cat in [("att", "ATT"), ("def", "DEF"), ("gard", "GARD"), ("banc_att", "ATT"), ("banc_def", "DEF"), ("banc_gard", "GARD")]:
                        if f"{slg}_df_{role}" in st.session_state:
                            cur_df = st.session_state[f"{slg}_df_{role}"]
                            for idx_p, row_p in cur_df.iterrows():
                                t_r, age_r, pj_r, pts_r = get_player_live_stats(row_p["Joueurs"], row_p["T"], row_p.get("A", 0), pos_cat)
                                cur_df.at[idx_p, "T"] = t_r; cur_df.at[idx_p, "A"] = age_r; cur_df.at[idx_p, "PJ"] = pj_r; cur_df.at[idx_p, "Pts"] = pts_r
                            st.session_state[f"{slg}_df_{role}"] = cur_df.sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                for slg in SLUG_MAP.values(): enforce_roster_limits(slg)
                save_state(official_api_updated=True, push_github=True)
                st.rerun()
        else:
            st.button("🔄 ACTUALISER API", use_container_width=True, disabled=True, help="🔒 Réservé au commissaire.")

    with c3:
        if is_admin:
            if st.button("📥 RECHARGER BASE", use_container_width=True, type="primary", help="Réinitialise les alignements, recrues et grilles de repêchage depuis les fichiers CSV/Excel."):
                init_rosters_from_csv()
                init_recrues_from_csv()
                init_drafts_from_excel()
                st.session_state["app_trade_history"] = []
                st.session_state["last_trade_backup"] = None
                for slg in SLUG_MAP.values(): enforce_roster_limits(slg)
                save_state(official_api_updated=True, push_github=True)
                st.success("✅ Base de données rechargée avec succès !")
                st.rerun()
        else:
            st.button("📥 RECHARGER BASE", use_container_width=True, disabled=True, help="🔒 Réservé au commissaire.")

    c_tog, c_btn = st.columns(2)
    with c_tog: afficher_couleurs = st.toggle("🎨 **Afficher les couleurs d'alerte de remplacement**", value=True)
    with c_btn:
        if is_admin:
            if st.button("⚡ **APPLIQUER LES PERMUTATIONS (1 POUR 1)**", use_container_width=True):
                for a_df, b_df, rl, gl, ka, kb in [(df_att, df_banc_att, r_att, g_att, f"{team_slug}_df_att", f"{team_slug}_df_banc_att"), (df_def, df_banc_def, r_def, g_def, f"{team_slug}_df_def", f"{team_slug}_df_banc_def"), (df_gard, df_banc_gard, r_gard, g_gard, f"{team_slug}_df_gard", f"{team_slug}_df_banc_gard")]:
                    if rl and gl:
                        st.session_state[ka] = pd.concat([a_df[~a_df["Joueurs"].isin(rl)], b_df[b_df["Joueurs"].isin(gl)]]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        st.session_state[kb] = pd.concat([b_df[~b_df["Joueurs"].isin(gl)], a_df[a_df["Joueurs"].isin(rl)]]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        st.session_state[ka]["No"], st.session_state[kb]["No"] = range(1, len(st.session_state[ka])+1), range(1, len(st.session_state[kb])+1)
                save_state(push_github=True)
                st.rerun()
        else:
            st.button("⚡ **APPLIQUER LES PERMUTATIONS (1 POUR 1)**", use_container_width=True, disabled=True, help="🔒 Réservé au commissaire.")

    if is_admin:
        with st.expander("🛠️ PERMUTATION MANUELLE (Monter, descendre ou combler)"):
            pos_choix = st.radio("Position à modifier :", ["Attaquants", "Défenseurs", "Gardiens"], horizontal=True, key=f"man_pos_{team_slug}")
            if pos_choix == "Attaquants":
                df_p, df_b, k_p, k_b, min_p, max_p = df_att, df_banc_att, f"{team_slug}_df_att", f"{team_slug}_df_banc_att", 19, 25
            elif pos_choix == "Défenseurs":
                df_p, df_b, k_p, k_b, min_p, max_p = df_def, df_banc_def, f"{team_slug}_df_def", f"{team_slug}_df_banc_def", 7, 11
            else:
                df_p, df_b, k_p, k_b, min_p, max_p = df_gard, df_banc_gard, f"{team_slug}_df_gard", f"{team_slug}_df_banc_gard", 2, 4

            real_p_list = [p for p in df_p["Joueurs"].tolist() if p != "[Carreau Libre]"]
            real_b_list = df_b["Joueurs"].tolist()

            p_choices = real_p_list.copy()
            if len(real_p_list) < max_p: p_choices = ["[Combler un carreau libre (Aucun départ)]"] + p_choices
            b_choices = real_b_list.copy()
            if len(real_p_list) > min_p: b_choices = ["[Laisser un carreau libre (Aucune montée)]"] + b_choices

            col_m1, col_m2, col_m3 = st.columns([2.5, 2.5, 1.2], vertical_alignment="bottom")
            with col_m1: p_sel = st.selectbox("📉 Joueur partant à descendre au banc :", p_choices, key=f"p_sel_{team_slug}")
            with col_m2: b_sel = st.selectbox("📈 Joueur du banc à monter partant :", b_choices, key=f"b_sel_{team_slug}")
            with col_m3:
                if st.button("🔄 EXÉCUTER", key=f"btn_perm_{team_slug}", use_container_width=True):
                    if p_sel.startswith("[Combler") and not b_sel.startswith("[Laisser"):
                        row_b = df_b[df_b["Joueurs"] == b_sel].copy()
                        new_b = df_b[df_b["Joueurs"] != b_sel].reset_index(drop=True)
                        new_p = pd.concat([df_p[df_p["Joueurs"] != "[Carreau Libre]"], row_b]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_p["No"], new_b["No"] = range(1, len(new_p) + 1), range(1, len(new_b) + 1)
                        st.session_state[k_p], st.session_state[k_b] = new_p, new_b
                        save_state(push_github=True); st.rerun()
                    elif b_sel.startswith("[Laisser") and not p_sel.startswith("[Combler"):
                        row_p = df_p[df_p["Joueurs"] == p_sel].copy()
                        new_p = df_p[df_p["Joueurs"] != p_sel].sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_b = pd.concat([df_b, row_p]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_p["No"], new_b["No"] = range(1, len(new_p) + 1), range(1, len(new_b) + 1)
                        st.session_state[k_p], st.session_state[k_b] = new_p, new_b
                        save_state(push_github=True); st.rerun()
                    elif not p_sel.startswith("[Combler") and not b_sel.startswith("[Laisser"):
                        row_p = df_p[df_p["Joueurs"] == p_sel].copy()
                        row_b = df_b[df_b["Joueurs"] == b_sel].copy()
                        new_p = pd.concat([df_p[df_p["Joueurs"] != p_sel], row_b]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_b = pd.concat([df_b[df_b["Joueurs"] != b_sel], row_p]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_p["No"], new_b["No"] = range(1, len(new_p) + 1), range(1, len(new_b) + 1)
                        st.session_state[k_p], st.session_state[k_b] = new_p, new_b
                        save_state(push_github=True); st.rerun()
    else:
        st.info("ℹ️ **Mode Consultation (Lecture seule)** : L'alignement et les permutations sont réservés au commissaire.")

    def fill_empty(df, m):
        if len(df) < m: return pd.concat([df, pd.DataFrame([{"No": i, "Joueurs": "[Carreau Libre]", "T": "—", "A": 0, "EXP": 0, "PJ": 0, "Pts": 0} for i in range(len(df)+1, m+1)])], ignore_index=True)
        return df.copy()

    df_att_disp, df_def_disp, df_gard_disp = fill_empty(df_att, 25), fill_empty(df_def, 11), fill_empty(df_gard, 4)

    def color_part(r, rl): return ['background-color: transparent; color: #475569; font-style: italic']*len(r) if r["Joueurs"]=="[Carreau Libre]" else (['background-color: #7F1D1D; color: #FECACA; font-weight: bold']*len(r) if afficher_couleurs and r["Joueurs"] in rl else ['background-color: rgba(15,23,42,0.6); color: #F8FAFC']*len(r))
    def color_banc(r, gl): return ['background-color: #064E3B; color: #D1FAE5; font-weight: bold']*len(r) if afficher_couleurs and r["Joueurs"] in gl else (['background-color: rgba(30,41,59,0.4); color: #94A3B8']*len(r))

    COL_CONF = {"No": st.column_config.NumberColumn("No", alignment="center"), "Joueurs": st.column_config.TextColumn("Joueurs", alignment="left"), "T": st.column_config.TextColumn("T", alignment="center"), "A": st.column_config.NumberColumn("A", alignment="center"), "EXP": st.column_config.NumberColumn("EXP", alignment="center"), "PJ": st.column_config.NumberColumn("PJ", alignment="center"), "Pts": st.column_config.NumberColumn("Pts", alignment="center")}

    co1, co2, co3 = st.columns([1, 1, 1])
    with co1:
        st.subheader(f"🏒 Attaquants Partants ({len(df_att)} / 25)")
        st.dataframe(df_att_disp.style.apply(color_part, rl=r_att, axis=1), hide_index=True, use_container_width=True, height=540, column_config=COL_CONF)
        st.markdown(f"<div style='color:{t['primary']}; font-weight:900;'>Total Attaquants : {int(df_att['Pts'].sum())} pts</div>", unsafe_allow_html=True)
        st.divider()
        st.subheader(f"🛡️ Défenseurs Partants ({len(df_def)} / 11)")
        st.dataframe(df_def_disp.style.apply(color_part, rl=r_def, axis=1), hide_index=True, use_container_width=True, height=280, column_config=COL_CONF)
        st.markdown(f"<div style='color:{t['primary']}; font-weight:900;'>Total Défenseurs : {int(df_def['Pts'].sum())} pts</div>", unsafe_allow_html=True)
        st.divider()
        st.subheader(f"🥅 Gardiens Partants ({len(df_gard)} / 4)")
        st.dataframe(df_gard_disp.style.apply(color_part, rl=r_gard, axis=1), hide_index=True, use_container_width=True, height=160, column_config=COL_CONF)
        st.markdown(f"<div style='color:{t['primary']}; font-weight:900;'>Total Gardiens : {int(df_gard['Pts'].sum())} pts</div>", unsafe_allow_html=True)
        st.divider()
        st.markdown(f"<div style='background:{t['btn_grad']}; color:white; padding:15px; border-radius:8px; text-align:center; font-size:1.2rem; font-weight:900; box-shadow: 0 4px 15px {t['primary_glow']}; border: 1px solid {t['primary']};'>TOTAL ÉQUIPE ACTIVE : {int(df_att['Pts'].sum() + df_def['Pts'].sum() + df_gard['Pts'].sum())} PTS</div>", unsafe_allow_html=True)
    with co2:
        st.subheader(f"📋 Attaquants Banc ({len(df_banc_att)})")
        st.dataframe(df_banc_att.style.apply(color_banc, gl=g_att, axis=1), hide_index=True, use_container_width=True, height=950, column_config=COL_CONF)
    with co3:
        st.subheader(f"🛡️ Défenseurs Banc ({len(df_banc_def)})")
        st.dataframe(df_banc_def.style.apply(color_banc, gl=g_def, axis=1), hide_index=True, use_container_width=True, height=480, column_config=COL_CONF)
        st.divider()
        st.subheader(f"🥅 Gardiens Banc ({len(df_banc_gard)})")
        st.dataframe(df_banc_gard.style.apply(color_banc, gl=g_gard, axis=1), hide_index=True, use_container_width=True, height=400, column_config=COL_CONF)

# =========================================================
# ONGLET 2 : CENTRE DES TRANSACTIONS (ÉCHANGES DIRECTS + ARCHIVES)
# =========================================================
with tab_transactions:
    st.markdown(f"<h1 style='color:{t['primary']};'>🔄 CENTRE OFFICIEL DES TRANSACTIONS</h1>", unsafe_allow_html=True)

    if is_admin:
        with st.expander("🔁 EFFECTUER UN NOUVEL ÉCHANGE DIRECT (JOUEURS, RECRUES & CHOIX 2027-2028)", expanded=True):
            team_list = list(POOLERS.keys())
            col_t1, col_t2, col_undo = st.columns([2, 2, 1.3], vertical_alignment="bottom")

            with col_t1:
                trade_team_a = st.selectbox("🏒 Équipe A :", team_list, index=0, key="trade_sel_a")
            with col_t2:
                other_teams = [tm for tm in team_list if tm != trade_team_a]
                trade_team_b = st.selectbox("🏒 Équipe B :", other_teams, index=0, key="trade_sel_b")
            with col_undo:
                has_backup = st.session_state.get("last_trade_backup") is not None
                if st.button("↩️ ANNULER DERNIER ÉCHANGE", use_container_width=True, disabled=not has_backup, help="Restaure exactement l'état juste avant la dernière transaction."):
                    if undo_last_trade():
                        st.success("✅ Dernière transaction annulée! Alignements et choix restaurés.")
                        st.rerun()

            players_a, picks_a = get_team_trade_assets(trade_team_a)
            players_b, picks_b = get_team_trade_assets(trade_team_b)

            map_p_a = {p["label"]: p for p in players_a}
            map_pk_a = {pk["label"]: pk for pk in picks_a}
            map_p_b = {p["label"]: p for p in players_b}
            map_pk_b = {pk["label"]: pk for pk in picks_b}

            col_a_box, col_b_box = st.columns(2)
            with col_a_box:
                st.markdown(f"#### 📤 Ce que **{trade_team_a}** cède :")
                sel_p_a_labels = st.multiselect("Joueurs / Recrues de l'équipe A :", options=list(map_p_a.keys()), key=f"ms_p_a_{SLUG_MAP[trade_team_a]}")
                sel_pk_a_labels = st.multiselect("Choix de repêchage (2027/2028) de l'équipe A :", options=list(map_pk_a.keys()), key=f"ms_pk_a_{SLUG_MAP[trade_team_a]}")

            with col_b_box:
                st.markdown(f"#### 📤 Ce que **{trade_team_b}** cède :")
                sel_p_b_labels = st.multiselect("Joueurs / Recrues de l'équipe B :", options=list(map_p_b.keys()), key=f"ms_p_b_{SLUG_MAP[trade_team_b]}")
                sel_pk_b_labels = st.multiselect("Choix de repêchage (2027/2028) de l'équipe B :", options=list(map_pk_b.keys()), key=f"ms_pk_b_{SLUG_MAP[trade_team_b]}")

            out_a = [map_p_a[l] for l in sel_p_a_labels]
            out_b = [map_p_b[l] for l in sel_p_b_labels]
            pk_out_a = [map_pk_a[l] for l in sel_pk_a_labels]
            pk_out_b = [map_pk_b[l] for l in sel_pk_b_labels]

            has_selection = bool(out_a or out_b or pk_out_a or pk_out_b)

            if has_selection:
                st.markdown("---")
                sim = simulate_trade(trade_team_a, trade_team_b, out_a, out_b, pk_out_a, pk_out_b)

                st.markdown("### 🔍 APERÇU DE L'IMPACT SUR LES FORMATIONS")

                prev1, prev2 = st.columns(2)
                with prev1:
                    sa = sim["stats_a"]
                    st.info(
                        f"**Alignement prévu pour {trade_team_a} :**\n\n"
                        f"• Attaquants : **{sa['att'][0]}** partants / {sa['att'][1]} au total\n\n"
                        f"• Défenseurs : **{sa['def'][0]}** partants / {sa['def'][1]} au total\n\n"
                        f"• Gardiens : **{sa['gard'][0]}** partants / {sa['gard'][1]} au total\n\n"
                        f"• Total Concession : **{sa['total']} / 128**"
                    )
                with prev2:
                    sb = sim["stats_b"]
                    st.info(
                        f"**Alignement prévu pour {trade_team_b} :**\n\n"
                        f"• Attaquants : **{sb['att'][0]}** partants / {sb['att'][1]} au total\n\n"
                        f"• Défenseurs : **{sb['def'][0]}** partants / {sb['def'][1]} au total\n\n"
                        f"• Gardiens : **{sb['gard'][0]}** partants / {sb['gard'][1]} au total\n\n"
                        f"• Total Concession : **{sb['total']} / 128**"
                    )

                if sim["notes"]:
                    details_txt = "\n".join([f"- {n}" for n in sim["notes"]])
                    st.markdown(f"**Mouvements de personnel enregistrés :**\n{details_txt}")

                for err in sim["errors"]:
                    st.error(err)
                for wrn in sim["warnings"]:
                    st.warning(wrn)

                can_execute = (len(sim["errors"]) == 0)

                if can_execute:
                    if st.button("✅ CONFIRMER ET EXÉCUTER LA TRANSACTION", type="primary", use_container_width=True):
                        execute_trade(trade_team_a, trade_team_b, out_a, out_b, pk_out_a, pk_out_b, sim)
                        st.success("🎉 Transaction validée et enregistrée ! Les effectifs ont été mis à jour.")
                        st.rerun()
                else:
                    st.button("🚫 TRANSACTION INTERDITE (QUOTAS NON RESPECTÉS)", disabled=True, use_container_width=True)
    else:
        st.info("ℹ️ **Mode Consultation** : Connectez-vous avec le mot de passe commissaire dans le menu latéral pour enregistrer un échange en direct.")

    st.divider()

    st.markdown(f"<h3 style='color:{t['primary']}; font-weight:900;'>🆕 TRANSACTIONS ENREGISTRÉES SUR LA PLATEFORME</h3>", unsafe_allow_html=True)
    app_hist = st.session_state.get("app_trade_history", [])
    if app_hist:
        df_app_hist = pd.DataFrame(app_hist)
        st.dataframe(df_app_hist, use_container_width=True, hide_index=True)
        csv_bytes = df_app_hist.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="📥 Télécharger l'historique des transactions (CSV / Excel)",
            data=csv_bytes,
            file_name=f"transactions_phdm_{datetime.now().strftime('%Y-%m-%d')}.csv",
            mime="text/csv"
        )
    else:
        st.caption("Aucune nouvelle transaction effectuée sur la plateforme depuis le dernier chargement de la base.")

    st.divider()

    st.markdown(f"<h3 style='color:#94A3B8; font-weight:900;'>📜 ARCHIVES HISTORIQUES DES TRANSACTIONS (EXCEL)</h3>", unsafe_allow_html=True)
    path_excel = get_file_path("gestion_phdm.xlsx")

    if os.path.exists(path_excel):
        try:
            xls = pd.ExcelFile(path_excel)
            sheet_name = find_sheet_by_keyword(path_excel, ["transaction", "echange", "trade"])

            if sheet_name:
                df_t_show = pd.read_excel(path_excel, sheet_name=sheet_name)
                df_t_show = clean_excel_dataframe(df_t_show)

                cols_date = [c for c in df_t_show.columns if "date" in str(c).lower()]
                col_target = cols_date[0] if cols_date else (df_t_show.columns[0] if len(df_t_show.columns) > 0 else None)

                if col_target:
                    df_t_show[col_target] = df_t_show[col_target].apply(format_date_fr)

                styler_t = df_t_show.style
                try: styler_t = styler_t.map(color_empty_cells)
                except AttributeError: styler_t = styler_t.applymap(color_empty_cells)
                st.dataframe(styler_t, use_container_width=True, hide_index=True)
            else:
                st.warning(f"⚠️ Aucun onglet de transactions détecté dans `{os.path.basename(path_excel)}`.")
                st.info(f"📋 **Onglets présents :** {', '.join([f'`{s}`' for s in xls.sheet_names])}")

        except PermissionError:
            st.error("⚠️ Le fichier Excel est actuellement ouvert ! Fermez Microsoft Excel puis rafraîchissez la page.")
        except Exception as e:
            st.warning(f"Impossible de lire le fichier de transactions : {e}")
    else:
        st.info("Fichier `gestion_phdm.xlsx` introuvable.")

# =========================================================
# ONGLET 3 : RÈGLEMENTS DU POOL
# =========================================================
with tab_reglements:
    st.markdown(f"""
    <div style='background: linear-gradient(135deg, #1E1B4B, #0F172A); border: 2px solid {t['primary']}; padding: 20px; border-radius: 14px; margin-bottom: 25px; text-align: center; box-shadow: 0 4px 20px rgba(0,0,0,0.5);'>
        <h1 style='margin:0; font-weight:900; color: #FFFFFF; font-size: 2.3rem; letter-spacing: 2px;'>📜 RÈGLEMENTS OFFICIELS DU POOL PHDM</h1>
        <div style='background: #EA580C; color: #FFFFFF; font-weight: 900; display: inline-block; padding: 6px 20px; border-radius: 20px; font-size: 1.05rem; margin-top: 10px; letter-spacing: 1px;'>
            SAISON OFFICIELLE 2026-2027 • SYNCHRONISÉ AVEC EXCEL
        </div>
    </div>
    """, unsafe_allow_html=True)

    path_excel = get_file_path("gestion_phdm.xlsx")
    reglements_loaded = False

    if os.path.exists(path_excel):
        try:
            xls = pd.ExcelFile(path_excel)
            sheet_reg = find_sheet_by_keyword(path_excel, ["reglement", "regle"])

            if sheet_reg:
                df_reg_raw = pd.read_excel(path_excel, sheet_name=sheet_reg, header=None)
                df_reg_raw = df_reg_raw.dropna(how="all").reset_index(drop=True)

                if not df_reg_raw.empty:
                    reglements_loaded = True
                    c_search, c_mode = st.columns([3, 1], vertical_alignment="center")
                    with c_search:
                        rech_reg = st.text_input("🔎 Rechercher dans les règlements (mot-clé, numéro d'article...) :", "", key="reg_search_box_phdm")
                    with c_mode:
                        vue_tableau = st.toggle("📋 Vue Grille / Tableau", value=False)

                    if vue_tableau:
                        df_reg_disp = clean_excel_dataframe(df_reg_raw)
                        if rech_reg:
                            mask = df_reg_disp.astype(str).apply(lambda col: col.str.contains(rech_reg, case=False, na=False)).any(axis=1)
                            df_reg_disp = df_reg_disp[mask]
                        st.dataframe(df_reg_disp, use_container_width=True, hide_index=True)
                    else:
                        current_section_title = "⚖️ DISPOSITIONS GÉNÉRALES"
                        current_section_items = []
                        sections = []

                        for _, row in df_reg_raw.iterrows():
                            vals = [str(x).strip() for x in row.dropna().values if str(x).strip().lower() not in ['nan', 'none', '']]
                            if not vals: continue
                            ligne_texte = " — ".join(vals)

                            if rech_reg and clean_str(rech_reg) not in clean_str(ligne_texte):
                                continue

                            first_val_up = vals[0].upper()
                            is_section_header = (
                                len(vals) == 1 and (
                                    any(first_val_up.startswith(k) for k in ["SECTION", "CHAPITRE", "PARTIE", "TITRE"]) or
                                    ("ART 00" in first_val_up or "ARTICLE 00" in first_val_up)
                                )
                            )

                            if is_section_header:
                                if current_section_items:
                                    sections.append((current_section_title, current_section_items))
                                    current_section_items = []
                                current_section_title = vals[0]
                            else:
                                current_section_items.append(vals)

                        if current_section_items:
                            sections.append((current_section_title, current_section_items))

                        if not sections:
                            st.info("Aucun règlement trouvé correspondant à votre recherche.")
                        else:
                            for sec_title, sec_items in sections:
                                with st.expander(f"📌 {sec_title.upper()}", expanded=True):
                                    for item in sec_items:
                                        if len(item) == 1:
                                            txt = item[0]
                                            if any(txt.upper().startswith(k) for k in ["ART.", "ART ", "ARTICLE"]):
                                                st.markdown(f"• **{txt}**")
                                            else:
                                                st.markdown(f"<div style='margin-left: 18px; color: #CBD5E1; margin-bottom: 6px;'>{txt}</div>", unsafe_allow_html=True)
                                        elif len(item) == 2:
                                            st.markdown(f"• <b style='color:{t['primary']};'>{item[0]} :</b> <span style='color:#F8FAFC;'>{item[1]}</span>", unsafe_allow_html=True)
                                        else:
                                            st.markdown(f"• <b style='color:{t['primary']};'>{item[0]}</b> | {' | '.join(item[1:])}")

            else:
                st.warning(f"⚠️ Aucun onglet `Reglements` trouvé dans `{os.path.basename(path_excel)}`.")
                st.info(f"📋 **Onglets présents dans Excel :** {', '.join([f'`{s}`' for s in xls.sheet_names])}")

        except PermissionError:
            st.error("⚠️ Le fichier Excel est actuellement ouvert ! Fermez Microsoft Excel puis rafraîchissez la page.")
        except Exception as e:
            st.error(f"Erreur lors de la lecture des règlements : {e}")

    if not reglements_loaded and not os.path.exists(path_excel):
        st.info("Fichier `gestion_phdm.xlsx` introuvable pour charger les règlements.")

# =========================================================
# ONGLET 4 : CHOIX DE REPÊCHAGE & ENCAN COMPLET (2027 & 2028)
# =========================================================
DRAFT_TEAM_STYLES = {
    "PHI": {"bg": "#EA580C", "color": "#FFFFFF", "border": "#C2410C"},
    "DAL": {"bg": "#16A34A", "color": "#FFFFFF", "border": "#15803D"},
    "SEA": {"bg": "#0D9488", "color": "#FFFFFF", "border": "#0F766E"},
    "STL": {"bg": "#2563EB", "color": "#FFFFFF", "border": "#1D4ED8"},
    "VGK": {"bg": "#CA8A04", "color": "#FFFFFF", "border": "#A16207"},
    "NYR": {"bg": "#0284C7", "color": "#FFFFFF", "border": "#0369A1"},
    "BUF": {"bg": "#1E1B4B", "color": "#FACC15", "border": "#FACC15"},
    "VAN": {"bg": "#4F46E5", "color": "#FFFFFF", "border": "#4338CA"},
    "MTL": {"bg": "#DC2626", "color": "#FFFFFF", "border": "#B91C1C"},
    "CHI": {"bg": "#18181B", "color": "#FFFFFF", "border": "#EF4444"},
    "MCW": {"bg": "#881337", "color": "#FFFFFF", "border": "#701A75"},
    "UCK": {"bg": "#881337", "color": "#FFFFFF", "border": "#701A75"},
}

with tab_repechage:
    c_head, c_ctrl1, c_ctrl2 = st.columns([1.5, 1.4, 1.1], vertical_alignment="center")
    with c_head:
        st.markdown("<h2 style='margin: 0; padding: 0; font-weight: 900; color: #F8FAFC; text-shadow: 0 0 10px rgba(255,255,255,0.2);'>🎟️ CENTRE DU REPÊCHAGE</h2>", unsafe_allow_html=True)

    available_years = [yr for yr in DRAFT_YEARS if f"df_draft_{yr}" in st.session_state]

    if available_years:
        with c_ctrl1:
            cohorte_labels = [f"Repêchage {yr}" for yr in available_years]
            onglet_choisi = st.radio("Sélectionnez la cohorte :", cohorte_labels, horizontal=True, label_visibility="collapsed")
        with c_ctrl2:
            e_filtre = st.selectbox("🔎 Mettre en valeur (DG) :", ["Toutes les équipes"] + list(TEAM_ABBR_MAP.values()), label_visibility="collapsed", key="filt_draft_live_phdm")

        annee_cohorte = onglet_choisi.replace("Repêchage ", "").strip()
        df_draft_live = st.session_state[f"df_draft_{annee_cohorte}"].copy()

        draft_ownership_map = {}
        for r_label, r_series in df_draft_live.iterrows():
            m_r = re.search(r'\d+', str(r_label))
            rd_num = int(m_r.group(0)) if m_r else 1
            for orig_abbr in TEAM_ABBR_MAP.values():
                if orig_abbr in df_draft_live.columns:
                    owner_abbr = to_team_abbr(r_series[orig_abbr]) or orig_abbr
                    draft_ownership_map[(rd_num, orig_abbr)] = owner_abbr

        total_rounds = max(15, len(df_draft_live))
        all_owned_picks = list(draft_ownership_map.values())
        counts = pd.Series(all_owned_picks).value_counts()

        st.markdown("---")

        titre_dash = "CAPITAL DE REPÊCHAGE GLOBAL" if e_filtre == "Toutes les équipes" else f"CAPITAL DE REPÊCHAGE : {e_filtre}"
        st.markdown(f"<h4 style='color:{t['primary']}; font-weight:900; text-transform:uppercase; margin-bottom:15px; font-size:1.05rem; letter-spacing:1px;'>📊 {titre_dash} ({onglet_choisi.upper()})</h4>", unsafe_allow_html=True)

        badges_list = []
        for team_abbr in TEAM_ABBR_MAP.values():
            count = counts.get(team_abbr, 0)
            info = DRAFT_TEAM_STYLES.get(team_abbr, {"bg": "#334155", "color": "#FFFFFF", "border": "#475569"})
            opacity = "1.0" if (e_filtre == "Toutes les équipes" or team_abbr == e_filtre) else "0.25"
            box_shadow = f"box-shadow: 0 0 20px {info['bg']}; transform: scale(1.05);" if (e_filtre == team_abbr) else "box-shadow: 0 4px 10px rgba(0,0,0,0.4);"

            badge_item = (
                f"<div style='background-color:{info['bg']}; color:{info['color']}; border:1px solid {info['border']}; "
                f"border-radius:12px; padding:10px 15px; display:inline-flex; flex-direction:column; align-items:center; "
                f"justify-content:center; min-width:95px; {box_shadow} opacity:{opacity}; transition:all 0.3s ease;'>"
                f"<span style='font-size:1.3rem; font-weight:900; letter-spacing:1px; margin-bottom:8px; text-shadow:0 2px 4px rgba(0,0,0,0.5);'>{team_abbr}</span>"
                f"<span style='background-color:rgba(0,0,0,0.4); color:#FFFFFF; border-radius:20px; padding:4px 14px; font-size:0.95rem; font-weight:800; box-shadow:inset 0 2px 6px rgba(0,0,0,0.6);'>{count} CHOIX</span>"
                f"</div>"
            )
            badges_list.append(badge_item)

        badges_html = f"<div style='display:flex; flex-wrap:wrap; gap:15px; margin-bottom:25px; justify-content:flex-start;'>{''.join(badges_list)}</div>"
        st.markdown(badges_html, unsafe_allow_html=True)

        chrono_rows = []
        pick_overall = 1

        for r_idx in range(total_rounds):
            r_num = r_idx + 1
            for rank_in_round, orig_team_meta in enumerate(draft_base_order, 1):
                orig_team = orig_team_meta["team"]
                orig_abbr = TEAM_ABBR_MAP.get(orig_team, "")
                orig_score = orig_team_meta["total"]
                owner_abbr = draft_ownership_map.get((r_num, orig_abbr), orig_abbr)
                owner_team = ABBR_TO_TEAM.get(owner_abbr, owner_abbr)
                owner_dg = POOLERS.get(owner_team, "N/A")
                is_traded = (owner_abbr != orig_abbr)

                statut = f"🔁 Acquis de {orig_abbr}" if is_traded else "✅ Choix d'origine"

                chrono_rows.append({
                    "Choix": pick_overall,
                    "Ronde": f"Ronde {r_num}",
                    "Rang": rank_in_round,
                    "Équipe Repêchant": f"{owner_team} ({owner_abbr})",
                    "Propriétaire (DG)": owner_dg,
                    "Choix Original De": f"{orig_team} ({orig_score} pts)",
                    "Statut": statut,
                    "_owner_abbr": owner_abbr,
                    "_r_num": r_num
                })
                pick_overall += 1

        df_chrono = pd.DataFrame(chrono_rows)

        st.markdown("""
        <div style='background:rgba(15,23,42,0.8); padding:15px; border-radius:12px; border:1px solid rgba(255,255,255,0.1); margin-bottom:20px;'>
            <h3 style='margin:0 0 10px 0; color:#38BDF8; font-weight:900;'>📜 DÉROULEMENT OFFICIEL DU REPÊCHAGE (ORDRE PAR RANG)</h3>
            <p style='color:#94A3B8; margin:0 0 15px 0; font-size:0.95rem;'>L'ordre de base commence par l'équipe ayant le <b>moins de points</b> au classement général (#1) et termine par la championne (#11). Tous les choix transigés appartiennent à leur propriétaire actuel.</p>
        </div>
        """, unsafe_allow_html=True)

        col_sel_ronde, col_info_rep = st.columns([1.5, 2.5], vertical_alignment="center")
        with col_sel_ronde:
            options_rondes = ["Toutes les rondes (1 à 15)"] + [f"Ronde {i}" for i in range(1, total_rounds + 1)]
            ronde_selectionnee = st.selectbox("🎯 **SÉLECTIONNER LA RONDE À AFFICHER :**", options_rondes, key="choix_ronde_box_phdm")

        df_view = df_chrono.copy()
        if e_filtre != "Toutes les équipes":
            df_view = df_view[df_view["_owner_abbr"] == e_filtre]
        if ronde_selectionnee != "Toutes les rondes (1 à 15)":
            df_view = df_view[df_view["Ronde"] == ronde_selectionnee]

        df_display = df_view.drop(columns=["_owner_abbr", "_r_num"]).reset_index(drop=True)

        with col_info_rep:
            st.markdown(f"<div style='font-weight:800; color:#F8FAFC; text-align:right;'>Nombre de choix affichés : <span style='color:{t['primary']}; font-size:1.2rem;'>{len(df_display)}</span></div>", unsafe_allow_html=True)

        def color_draft_board(row):
            if "🔁" in str(row["Statut"]):
                return ['background-color: rgba(30, 58, 138, 0.45); color: #93C5FD; font-weight: 700'] * len(row)
            return ['background-color: rgba(15, 23, 42, 0.6); color: #F8FAFC'] * len(row)

        chrono_config = {
            "Choix": st.column_config.NumberColumn("Choix #", format="#%d", width="small", alignment="center"),
            "Ronde": st.column_config.TextColumn("Ronde", width="small", alignment="center"),
            "Rang": st.column_config.NumberColumn("Rang Ronde", format="%d", width="small", alignment="center"),
            "Équipe Repêchant": st.column_config.TextColumn("Équipe qui repêche", width="medium"),
            "Propriétaire (DG)": st.column_config.TextColumn("DG Responsable", width="medium"),
            "Choix Original De": st.column_config.TextColumn("Choix d'origine (et pts)", width="medium"),
            "Statut": st.column_config.TextColumn("Statut du choix", width="medium", alignment="center"),
        }

        styler_c = df_display.style.apply(color_draft_board, axis=1)
        st.dataframe(styler_c, use_container_width=True, hide_index=True, height=450, column_config=chrono_config)

        st.divider()

        st.markdown(f"<h3 style='color:{t['primary']}; font-weight:900; margin: 20px 0 10px 0;'>📊 GRILLE MATRICIELLE OFFICIELLE ({onglet_choisi.upper()})</h3>", unsafe_allow_html=True)

        df_matrix_disp = df_draft_live.reset_index().rename(columns={"index": "Ronde"})

        def premium_draft_style(val):
            s = str(val).strip().upper()
            if s in ["", "NAN", "NONE"]:
                return "background-color: transparent; color: transparent;"
            is_highlighted = (e_filtre != "Toutes les équipes" and s == e_filtre)
            is_faded = (e_filtre != "Toutes les équipes" and s != e_filtre and s in TEAM_ABBR_MAP.values())

            if is_highlighted:
                info = DRAFT_TEAM_STYLES.get(s, {"bg": "#FACC15", "color": "#000000", "border": "#CA8A04"})
                return f"background-color: {info['bg']}; color: {info['color']}; font-weight: 900; border: 2px solid #FFFFFF; text-align: center; box-shadow: inset 0 0 20px rgba(255,255,255,0.6);"
            elif is_faded:
                return "background-color: rgba(15,23,42,0.4); color: rgba(255,255,255,0.15); font-weight: 400; border: 1px solid rgba(255,255,255,0.05); text-align: center;"
            elif s in DRAFT_TEAM_STYLES:
                info = DRAFT_TEAM_STYLES[s]
                return f"background-color: {info['bg']}; color: {info['color']}; font-weight: 800; border: 1px solid {info['border']}; text-align: center;"

            return f"background-color: {t['card_bg']}; color: {t['primary']}; text-align: center; font-weight: 900;"

        styler = df_matrix_disp.style
        try: styler = styler.map(premium_draft_style)
        except AttributeError: styler = styler.applymap(premium_draft_style)

        cols_config = {col: st.column_config.TextColumn(col, alignment="center") for col in df_matrix_disp.columns}
        st.dataframe(styler, use_container_width=True, hide_index=True, height=580, column_config=cols_config)
    else:
        st.warning("⚠️ Aucune grille de repêchage 2027 ou 2028 initialisée.")

# =========================================================
# ONGLET 5 : BANQUE DES RECRUES (32 ÉQUIPES LNH + PHDM)
# =========================================================
with tab_recrues:
    st.markdown(f"<h1 style='color:{t['primary']};'>🌱 BANQUE DES RECRUES (Amateurs / Espoirs)</h1>", unsafe_allow_html=True)
    df_all_rec = st.session_state.get("df_all_recrues", pd.DataFrame())
    if not df_all_rec.empty:
        df_show = df_all_rec.drop(columns=["Standard_PHDM"], errors="ignore").copy()
        vrais_lnh = df_show["Équipe LNH (Réelle)"].unique().tolist()
        if "Libre / Non repêché" in vrais_lnh: vrais_lnh.remove("Libre / Non repêché")
        def sort_teams_key(team_name):
            t_lower = str(team_name).lower()
            if "draft" in t_lower or "libre" in t_lower or "non rep" in t_lower: return "zzzz_" + t_lower
            return "".join([c for c in unicodedata.normalize('NFKD', str(team_name)) if not unicodedata.combining(c)]).lower()

        vrais_lnh = sorted(vrais_lnh, key=sort_teams_key)
        filtre = st.selectbox("🔎 SÉLECTIONNER UNE ÉQUIPE DE LA LNH :", ["Toutes les recrues"] + vrais_lnh)

        CONF_REC = {
            "No": st.column_config.NumberColumn("No", width="small", alignment="center"),
            "Joueurs": st.column_config.TextColumn("Nom du Joueur", width="medium"),
            "Pos": st.column_config.TextColumn("Pos", width="small", alignment="center"),
            "Année": st.column_config.TextColumn("Année", width="small", alignment="center"),
            "Ronde": st.column_config.TextColumn("Ronde", width="small", alignment="center"),
            "Rang": st.column_config.TextColumn("Rang", width="small", alignment="center"),
            "Cotes": st.column_config.TextColumn("Cotes Experts", width="small", alignment="center"),
            "Club Junior": st.column_config.TextColumn("Club Junior", width="medium"),
            "Ligue": st.column_config.TextColumn("Ligue", width="small", alignment="center"),
            "Équipe LNH (Réelle)": None,
            "Équipe PHDM": st.column_config.TextColumn("Appartient à (PHDM)", width="medium", alignment="center")
        }

        if filtre != "Toutes les recrues":
            df_show = df_show[df_show["Équipe LNH (Réelle)"] == filtre]
            logo, color = get_nhl_team_branding(filtre)
            st.markdown(f'''<div style="display:flex;align-items:center;background:rgba(30,41,59,0.7);backdrop-filter(blur(10px));padding:12px;border-left:8px solid {color};border-radius:12px;margin:10px 0 15px;box-shadow:0 4px 15px rgba(0,0,0,0.3);"><img src="{logo}" width="55" style="margin-right: 20px; filter: drop-shadow(0px 0px 5px rgba(255,255,255,0.2));"><div><h3 style="margin: 0; color: #F8FAFC; text-transform: uppercase; font-weight:900;">{filtre}</h3><div style="color: {t['primary']}; font-weight:700; font-size: 0.9rem; letter-spacing:1px;">ESPOIRS REPÊCHÉS ({len(df_show)} JOUEURS)</div></div></div>''', unsafe_allow_html=True)
            st.dataframe(df_show.style.apply(lambda r: ['background-color: transparent; color: #F8FAFC'] * len(r), axis=1), hide_index=True, use_container_width=True, height=650, column_config=CONF_REC)
        else:
            st.markdown(f"<h3 style='color:{t['primary']}; font-weight:900;'>TOUTES LES RECRUES ({len(df_show)} joueurs)</h3>", unsafe_allow_html=True)
            for eq in vrais_lnh:
                df_eq = df_show[df_show["Équipe LNH (Réelle)"] == eq]
                if df_eq.empty: continue
                logo, color = get_nhl_team_branding(eq)
                st.markdown(f'''<div style="display:flex;align-items:center;background:rgba(15,23,42,0.6);padding:10px 15px;border-left:6px solid {color};border-radius:8px;margin:30px 0 10px;box-shadow:0 2px 10px rgba(0,0,0,0.2);"><img src="{logo}" width="40" style="margin-right: 15px; filter: drop-shadow(0px 0px 3px rgba(255,255,255,0.1));"><h4 style="margin: 0; color: #F8FAFC; font-weight:800; text-transform: uppercase; letter-spacing:1px;">{eq} <span style="color:{t['primary']}; font-size:0.9rem;">({len(df_eq)})</span></h4></div>''', unsafe_allow_html=True)
                st.dataframe(df_eq.style.apply(lambda r: ['background-color: transparent; color: #E2E8F0'] * len(r), axis=1), hide_index=True, use_container_width=True, column_config=CONF_REC)

# =========================================================
# =========================================================
# ONGLET 6 : DIRECT DE LA JOURNÉE (STATISTIQUES NON OFFICIELLES)
# =========================================================
from concurrent.futures import ThreadPoolExecutor

def _nhl_txt(val):
    if isinstance(val, dict):
        return str(val.get("default", ""))
    return str(val or "")

def _norm_umlaut(s):
    return s.replace("ue", "u").replace("ae", "a").replace("oe", "o")

@st.cache_data(ttl=45)
def fetch_scoreboard_by_date_direct(date_str="now"):
    url = f"https://api-web.nhle.com/v1/score/{date_str}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}

@st.cache_data(ttl=45)
def fetch_day_details_parallel(game_ids_tuple):
    def fetch_one(gid):
        land, box = {}, {}
        try:
            r1 = requests.get(f"https://api-web.nhle.com/v1/gamecenter/{gid}/landing", headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if r1.status_code == 200:
                land = r1.json()
        except Exception:
            pass
        try:
            r2 = requests.get(f"https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore", headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if r2.status_code == 200:
                box = r2.json()
        except Exception:
            pass
        return gid, land, box

    results = {}
    if not game_ids_tuple:
        return results
    with ThreadPoolExecutor(max_workers=8) as ex:
        for gid, land, box in ex.map(fetch_one, game_ids_tuple):
            results[gid] = (land, box)
    return results

@st.cache_data(ttl=120)
def fetch_goalies_landing_parallel(player_ids_tuple):
    def fetch_g(pid):
        try:
            r = requests.get(f"https://api-web.nhle.com/v1/player/{pid}/landing", headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if r.status_code == 200:
                return pid, r.json()
        except Exception:
            pass
        return pid, {}

    results = {}
    if not player_ids_tuple:
        return results
    with ThreadPoolExecutor(max_workers=8) as ex:
        for pid, data in ex.map(fetch_g, player_ids_tuple):
            results[pid] = data
    return results

def _build_name_variants(nom_excel):
    nc = clean_str(nom_excel)
    variants = set()
    if not nc or nc == "carreau libre":
        return variants
    variants.add(nc)
    variants.add(_norm_umlaut(nc))
    if nc in ALIASES:
        variants.add(ALIASES[nc])
        variants.add(_norm_umlaut(ALIASES[nc]))
    parts = nc.split()
    if len(parts) >= 2:
        v1 = f"{' '.join(parts[1:])} {parts[0]}"
        v2 = f"{parts[-1]} {' '.join(parts[:-1])}"
        for v in [v1, v2]:
            variants.add(v)
            variants.add(_norm_umlaut(v))
            if v in ALIASES:
                variants.add(ALIASES[v])
                variants.add(_norm_umlaut(ALIASES[v]))
    return variants

def _match_live_skater(nom_excel, eq_lnh, live_skaters):
    """Trouve le joueur LNH même si son nom est inversé, accentué ou coupé dans le CSV."""
    variants = _build_name_variants(nom_excel)
    if not variants:
        return None

    # 1. Correspondance exacte (ou via ALIASES / inversion Nom-Prénom)
    exact_hits = []
    for lp in live_skaters:
        if lp["full"] in variants or lp["full_norm"] in variants:
            exact_hits.append(lp)
    if exact_hits:
        for lp in exact_hits:
            if eq_lnh and lp["team"] in eq_lnh:
                return lp
        return exact_hits[0]

    # 2. Correspondance intelligente (noms coupés/tronqués, sans espaces, initiales)
    nc = clean_str(nom_excel)
    parts = nc.split()
    candidate_splits = []
    if len(parts) >= 2:
        candidate_splits.append((parts[0], " ".join(parts[1:])))           # Nom + Prénom(s)
        candidate_splits.append((" ".join(parts[:-1]), parts[-1]))          # Nom composé + Prénom
        candidate_splits.append((parts[-1], " ".join(parts[:-1])))          # Prénom(s) + Nom
        candidate_splits.append((" ".join(parts[1:]), parts[0]))           # Prénom + Nom composé

    fuzzy_hits = []
    for lp in live_skaters:
        lp_last = lp["last"]
        lp_first = lp["first"]
        if not lp_last or not lp_first:
            continue
        lp_last_ns = lp_last.replace(" ", "")
        matched = False
        for p_last, p_first in candidate_splits:
            p_last_n = _norm_umlaut(p_last)
            p_first_n = _norm_umlaut(p_first)
            p_last_ns = p_last_n.replace(" ", "")

            last_ok = (
                p_last_n == lp_last
                or p_last_ns == lp_last_ns
                or (len(p_last_n) >= 6 and lp_last.startswith(p_last_n))
            )
            if not last_ok:
                continue

            if len(p_first_n) >= 2:
                first_ok = lp_first.startswith(p_first_n) or p_first_n.startswith(lp_first)
            else:
                first_ok = lp_first.startswith(p_first_n) and (not eq_lnh or lp["team"] in eq_lnh)

            if first_ok:
                matched = True
                break
        if matched:
            fuzzy_hits.append(lp)

    if fuzzy_hits:
        for lp in fuzzy_hits:
            if eq_lnh and lp["team"] in eq_lnh:
                return lp
        return fuzzy_hits[0]

    return None

def _match_live_goalie(nom_excel, eq_lnh, live_goalies):
    variants = _build_name_variants(nom_excel)
    for lg in live_goalies:
        if lg["full"] in variants or lg["full_norm"] in variants:
            return lg
    # Secours par nom coupé
    nc = clean_str(nom_excel)
    parts = nc.split()
    if len(parts) >= 2:
        for lg in live_goalies:
            for p_last, p_first in [(parts[0], " ".join(parts[1:])), (parts[-1], " ".join(parts[:-1]))]:
                if (p_last == lg["last"] or p_last.replace(" ", "") == lg["last"].replace(" ", "")) and lg["first"].startswith(p_first[:1]):
                    if len(p_first) >= 2 and lg["first"].startswith(p_first):
                        return lg
                    if len(p_first) == 1 and (not eq_lnh or lg["team"] in eq_lnh):
                        return lg
    return None

@st.fragment(run_every=60)
def render_tab_direct():
    st.markdown("""
    <div style='background: rgba(180, 83, 9, 0.28); border: 2px solid #F59E0B; border-radius: 12px; padding: 14px 18px; margin-bottom: 14px; text-align: center;'>
        <div style='font-size: 1.1rem; font-weight: 900; color: #FCD34D; letter-spacing: 0.5px;'>
            ⚠️ STATISTIQUES NON OFFICIELLES — DIRECT DE LA JOURNÉE
        </div>
        <div style='font-size: 0.9rem; color: #FDE68A; margin-top: 4px;'>
            Points calculés en direct pendant les matchs (sujets à révision par la LNH). L'onglet <b>Alignements & Équipes</b> conserve les vrais comptes officiels mis à jour la nuit après les matchs.
        </div>
    </div>
    """, unsafe_allow_html=True)

    now_data = fetch_scoreboard_by_date_direct("now")
    now_date = now_data.get("currentDate", "")
    prev_date = now_data.get("prevDate", "")

    col_d1, col_d2 = st.columns([1.4, 2.6], vertical_alignment="center")
    with col_d1:
        choix_jour = st.radio(
            "📅 Journée à afficher :",
            [f"Aujourd'hui ({now_date or 'En direct'})", f"Hier ({prev_date or 'Veille'})"],
            horizontal=True,
            key="direct_day_choice"
        )
    score_data = now_data if choix_jour.startswith("Aujourd'hui") else (fetch_scoreboard_by_date_direct(prev_date) if prev_date else now_data)
    current_nhl_date = score_data.get("currentDate", now_date)
    games = score_data.get("games", [])

    heure_qc = datetime.now(ZoneInfo("America/Toronto")).strftime("%H:%M:%S")
    last_maj = st.session_state.get("last_official_update_et", "")
    with col_d2:
        st.caption(f"🔄 Rafraîchi toutes les 60 s (dernier : {heure_qc} HE) • Date LNH affichée : **{current_nhl_date}** • Dernière MAJ officielle : {last_maj or 'Non enregistrée'}")

    if games:
        cols_g = st.columns(min(6, len(games)))
        for idx_g, g in enumerate(games):
            away = g.get("awayTeam", {})
            home = g.get("homeTeam", {})
            state = g.get("gameState", "")
            clock = g.get("clock", {}).get("timeRemaining", "")
            p_num = g.get("periodDescriptor", {}).get("number", "")
            if state in ("LIVE", "CRIT"):
                badge = f"🔴 P{p_num} {clock}"
            elif state in ("FINAL", "OFF"):
                lp = g.get("gameOutcome", {}).get("lastPeriodType", "")
                badge = f"✅ Final{' (' + lp + ')' if lp in ('OT', 'SO') else ''}"
            else:
                badge = "🕒 À venir"
            with cols_g[idx_g % len(cols_g)]:
                st.markdown(f"""
                <div style='background:rgba(15,23,42,0.75); border:1px solid rgba(255,255,255,0.12); border-radius:8px; padding:7px; margin-bottom:8px; text-align:center;'>
                    <div style='font-size:0.72rem; color:#94A3B8; font-weight:800;'>{badge}</div>
                    <div style='font-size:0.92rem; font-weight:900; color:#F8FAFC;'>{_nhl_txt(away.get('abbrev'))} {away.get('score', 0)} - {home.get('score', 0)} {_nhl_txt(home.get('abbrev'))}</div>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("Aucun match LNH au calendrier pour cette journée.")

    # Récupération en parallèle des fiches officielles (/landing + /boxscore) de chaque match actif/terminé
    active_gids = tuple(g.get("id") for g in games if g.get("id") and g.get("gameState") in ("LIVE", "CRIT", "FINAL", "OFF"))
    details_map = fetch_day_details_parallel(active_gids)

    skaters_by_pid = {}  # pid -> dict stats + noms complets
    all_games_final = bool(games)
    goalie_pids_to_check = []
    goalie_game_contexts = []

    for g in games:
        gid = g.get("id")
        state = g.get("gameState", "")
        if state not in ("FINAL", "OFF"):
            all_games_final = False
        if not gid or gid not in details_map:
            continue

        land, box = details_map[gid]
        away = g.get("awayTeam", {})
        home = g.get("homeTeam", {})
        away_ab = _nhl_txt(away.get("abbrev")).upper()
        home_ab = _nhl_txt(home.get("abbrev")).upper()

        # 1. Lecture de /landing -> summary.scoring (noms complets des buteurs et passeurs, hors tirs de barrage)
        scoring_periods = land.get("summary", {}).get("scoring", [])
        for per in scoring_periods:
            if per.get("periodDescriptor", {}).get("periodType") == "SO":
                continue
            for goal in per.get("goals", []):
                g_team = _nhl_txt(goal.get("teamAbbrev")).upper()
                b_pid = goal.get("playerId")
                b_fn = _nhl_txt(goal.get("firstName"))
                b_ln = _nhl_txt(goal.get("lastName"))
                if b_pid or (b_fn and b_ln):
                    key = b_pid or f"{b_fn}_{b_ln}_{g_team}"
                    if key not in skaters_by_pid:
                        skaters_by_pid[key] = {"pid": b_pid, "first_raw": b_fn, "last_raw": b_ln, "team": g_team, "but": 0, "assiste": 0}
                    skaters_by_pid[key]["but"] += 1

                for ast in goal.get("assists", []):
                    a_pid = ast.get("playerId")
                    a_fn = _nhl_txt(ast.get("firstName"))
                    a_ln = _nhl_txt(ast.get("lastName"))
                    if a_pid or (a_fn and a_ln):
                        akey = a_pid or f"{a_fn}_{a_ln}_{g_team}"
                        if akey not in skaters_by_pid:
                            skaters_by_pid[akey] = {"pid": a_pid, "first_raw": a_fn, "last_raw": a_ln, "team": g_team, "but": 0, "assiste": 0}
                        skaters_by_pid[akey]["assiste"] += 1

        # 2. Vérification croisée avec /boxscore (garantit le bon total de buts/passes même si /landing a un léger délai)
        pbg = box.get("playerByGameStats", {})
        for side_key, team_ab in [("awayTeam", away_ab), ("homeTeam", home_ab)]:
            side_data = pbg.get(side_key, {})
            for sk in side_data.get("forwards", []) + side_data.get("defense", []):
                g_cnt = int(sk.get("goals", 0) or 0)
                a_cnt = int(sk.get("assists", 0) or 0)
                if g_cnt == 0 and a_cnt == 0:
                    continue
                pid = sk.get("playerId")
                if pid and pid in skaters_by_pid:
                    skaters_by_pid[pid]["but"] = max(skaters_by_pid[pid]["but"], g_cnt)
                    skaters_by_pid[pid]["assiste"] = max(skaters_by_pid[pid]["assiste"], a_cnt)
                elif pid:
                    # Au cas où /landing n'avait pas encore ce joueur, on récupérera son nom complet via /player/{pid}/landing
                    skaters_by_pid[pid] = {"pid": pid, "first_raw": "", "last_raw": _nhl_txt(sk.get("name")), "team": team_ab, "but": g_cnt, "assiste": a_cnt, "_need_name": True}

        # 3. Gardiens pour matchs terminés
        if state in ("FINAL", "OFF"):
            away_sc = away.get("score", 0)
            home_sc = home.get("score", 0)
            last_period = g.get("gameOutcome", {}).get("lastPeriodType", "REG")
            win_gid = g.get("winningGoalie", {}).get("playerId")
            for side_key, team_ab, opp_score, won_game in [
                ("awayTeam", away_ab, home_sc, away_sc > home_sc),
                ("homeTeam", home_ab, away_sc, home_sc > away_sc)
            ]:
                g_list = pbg.get(side_key, {}).get("goalies", [])
                played = [gl for gl in g_list if gl.get("toi", "00:00") not in ("00:00", "0:00", "")]
                for gl in played:
                    pid = gl.get("playerId")
                    if pid:
                        goalie_pids_to_check.append(pid)
                        goalie_game_contexts.append({
                            "gid": gid, "pid": pid, "team": team_ab,
                            "opp_score": opp_score, "won_game": won_game,
                            "last_period": last_period, "win_gid": win_gid,
                            "is_last_played": (gl == played[-1]), "nb_played": len(played)
                        })

    # Complète les rares noms manquants si /boxscore a devancé /landing
    missing_name_pids = tuple(v["pid"] for v in skaters_by_pid.values() if v.get("_need_name") and v.get("pid"))
    all_pid_lookups = tuple(set(goalie_pids_to_check) | set(missing_name_pids))
    player_landings = fetch_goalies_landing_parallel(all_pid_lookups)

    for v in skaters_by_pid.values():
        if v.get("_need_name") and v.get("pid") in player_landings:
            pl = player_landings[v["pid"]]
            v["first_raw"] = _nhl_txt(pl.get("firstName")) or v["first_raw"]
            v["last_raw"] = _nhl_txt(pl.get("lastName")) or v["last_raw"]

    live_skaters = []
    for v in skaters_by_pid.values():
        fn_c = clean_str(v["first_raw"])
        ln_c = clean_str(v["last_raw"])
        full_c = f"{fn_c} {ln_c}".strip()
        live_skaters.append({
            "display": f"{v['first_raw']} {v['last_raw']}".strip(),
            "first": _norm_umlaut(fn_c),
            "last": _norm_umlaut(ln_c),
            "full": full_c,
            "full_norm": _norm_umlaut(full_c),
            "team": v["team"],
            "but": v["but"],
            "assiste": v["assiste"],
            "matched_in_pool": False
        })

    # Calcul des points des gardiens (2 pts V, 1 pt DP/DF, +1 pt BL)
    live_goalies_map = {}
    for ctx in goalie_game_contexts:
        pl = player_landings.get(ctx["pid"], {})
        fn = _nhl_txt(pl.get("firstName"))
        ln = _nhl_txt(pl.get("lastName"))
        fn_c = clean_str(fn)
        ln_c = clean_str(ln)
        full_c = f"{fn_c} {ln_c}".strip()
        if not full_c:
            continue

        pts_g = 0
        decision = ""
        for gm in pl.get("last5Games", []):
            if gm.get("gameId") == ctx["gid"]:
                decision = str(gm.get("decision", "")).upper()
                break

        if decision == "W" or (not decision and ctx["win_gid"] == ctx["pid"]):
            pts_g += 2
        elif decision in ("O", "OT", "OTL") or (not decision and not ctx["won_game"] and ctx["last_period"] in ("OT", "SO") and ctx["is_last_played"]):
            pts_g += 1

        if ctx["won_game"] and ctx["opp_score"] == 0 and ctx["nb_played"] == 1:
            pts_g += 1

        if pts_g > 0:
            if ctx["pid"] not in live_goalies_map:
                live_goalies_map[ctx["pid"]] = {
                    "display": f"{fn} {ln}".strip(),
                    "first": _norm_umlaut(fn_c),
                    "last": _norm_umlaut(ln_c),
                    "full": full_c,
                    "full_norm": _norm_umlaut(full_c),
                    "team": ctx["team"],
                    "pts": 0
                }
            live_goalies_map[ctx["pid"]]["pts"] += pts_g
    live_goalies = list(live_goalies_map.values())

    already_in_official = False
    if last_maj and current_nhl_date and all_games_final:
        maj_date = last_maj.split(" ")[0]
        if maj_date > current_nhl_date:
            already_in_official = True

    team_rows_summary = []
    team_players_detail = {}

    for team_name, slug in SLUG_MAP.items():
        pts_att = int(st.session_state.get(f"{slug}_df_att", pd.DataFrame([{"Pts": 0}]))["Pts"].sum())
        pts_def = int(st.session_state.get(f"{slug}_df_def", pd.DataFrame([{"Pts": 0}]))["Pts"].sum())
        pts_gard = int(st.session_state.get(f"{slug}_df_gard", pd.DataFrame([{"Pts": 0}]))["Pts"].sum())
        pts_club = pts_att + pts_def + pts_gard

        pts_journee_partants = 0
        player_rows = []

        role_defs = [
            ("att", "ATT", "Partant ✅", True),
            ("def", "DEF", "Partant ✅", True),
            ("gard", "GARD", "Partant ✅", True),
            ("banc_att", "ATT", "Banc 📋", False),
            ("banc_def", "DEF", "Banc 📋", False),
            ("banc_gard", "GARD", "Banc 📋", False),
        ]

        for role_key, pos_lbl, statut_lbl, is_partant in role_defs:
            df_r = st.session_state.get(f"{slug}_df_{role_key}", pd.DataFrame())
            if df_r.empty:
                continue
            for _, row in df_r.iterrows():
                nom = str(row.get("Joueurs", "")).strip()
                if not nom or nom == "[Carreau Libre]":
                    continue
                eq_lnh = str(row.get("T", "")).strip().upper()

                if pos_lbl == "GARD":
                    buts, passes, pts_j = 0, 0, 0
                    mg = _match_live_goalie(nom, eq_lnh, live_goalies)
                    if mg:
                        pts_j = mg["pts"]
                else:
                    buts, passes = 0, 0
                    ms = _match_live_skater(nom, eq_lnh, live_skaters)
                    if ms:
                        buts = ms["but"]
                        passes = ms["assiste"]
                        ms["matched_in_pool"] = True
                    pts_j = buts + passes

                if is_partant:
                    pts_journee_partants += pts_j

                player_rows.append({
                    "Nom du joueur": nom,
                    "Pos": pos_lbl,
                    "Statut": statut_lbl,
                    "Éq. LNH": eq_lnh,
                    "But": buts,
                    "Assiste": passes,
                    "Point": pts_j,
                    "_is_partant": 1 if is_partant else 0
                })

        total_prov = pts_club if already_in_official else (pts_club + pts_journee_partants)

        team_rows_summary.append({
            "Équipe": team_name,
            "Points de la journée": pts_journee_partants,
            "Point du club": pts_club,
            "Total": total_prov
        })

        df_p_detail = pd.DataFrame(player_rows)
        if not df_p_detail.empty:
            df_p_detail = df_p_detail.sort_values(
                by=["Point", "But", "_is_partant", "Nom du joueur"],
                ascending=[False, False, False, True]
            ).drop(columns=["_is_partant"]).reset_index(drop=True)
        team_players_detail[team_name] = df_p_detail

    df_standings_day = pd.DataFrame(team_rows_summary).sort_values(
        by=["Points de la journée", "Total", "Équipe"],
        ascending=[False, False, True]
    ).reset_index(drop=True)
    df_standings_day.insert(0, "Rang", range(1, len(df_standings_day) + 1))

    if already_in_official:
        st.info("ℹ️ La mise à jour officielle de la nuit a déjà intégré les matchs de cette journée dans « Point du club ». La colonne **Total** correspond donc déjà au vrai compte (sans compter en double).")

    col_gauche, col_droite = st.columns([1.15, 1.25], vertical_alignment="top")

    with col_gauche:
        st.markdown(f"<h3 style='color:{t['primary']}; font-weight:900; margin-bottom:10px;'>🏆 CLASSEMENT DE LA JOURNÉE (11 ÉQUIPES)</h3>", unsafe_allow_html=True)

        def style_day_standings(row):
            if row["Rang"] == 1 and row["Points de la journée"] > 0:
                return ["background-color: rgba(16, 185, 129, 0.22); color: #ECFDF5; font-weight: 900"] * len(row)
            return ["background-color: rgba(15, 23, 42, 0.72); color: #F8FAFC"] * len(row)

        st.dataframe(
            df_standings_day.style.apply(style_day_standings, axis=1),
            use_container_width=True,
            hide_index=True,
            height=435,
            column_config={
                "Rang": st.column_config.NumberColumn("Rang", width="small", alignment="center"),
                "Équipe": st.column_config.TextColumn("Équipe PHDM", width="medium"),
                "Points de la journée": st.column_config.NumberColumn("Points de la journée", alignment="center"),
                "Point du club": st.column_config.NumberColumn("Point du club", alignment="center"),
                "Total": st.column_config.NumberColumn("Total", alignment="center"),
            }
        )

    with col_droite:
        st.markdown(f"<h3 style='color:{t['primary']}; font-weight:900; margin-bottom:10px;'>🔎 JOUEURS DE L'ÉQUIPE (PARTANTS & BANC)</h3>", unsafe_allow_html=True)
        equipes_triees = df_standings_day["Équipe"].tolist()
        eq_choisie = st.selectbox("Choisir une équipe :", options=equipes_triees, key="direct_sel_team")

        df_p_show = team_players_detail.get(eq_choisie, pd.DataFrame())
        if not df_p_show.empty:
            pts_p = int(df_p_show[df_p_show["Statut"].str.contains("Partant")]["Point"].sum())
            pts_b = int(df_p_show[df_p_show["Statut"].str.contains("Banc")]["Point"].sum())
            st.markdown(f"<div style='margin-bottom:8px; font-weight:800; color:#CBD5E1;'>Partants : <span style='color:#34D399;'>{pts_p} pts</span> &nbsp;|&nbsp; Banc (ne compte pas) : <span style='color:#94A3B8;'>{pts_b} pts</span></div>", unsafe_allow_html=True)

            def style_player_day(row):
                if row["Point"] > 0 and "Partant" in row["Statut"]:
                    return ["background-color: rgba(16, 185, 129, 0.20); color: #ECFDF5; font-weight: 800"] * len(row)
                if row["Point"] > 0:
                    return ["background-color: rgba(245, 158, 11, 0.16); color: #FEF3C7; font-weight: 700"] * len(row)
                if "Banc" in row["Statut"]:
                    return ["background-color: rgba(30, 41, 59, 0.35); color: #94A3B8"] * len(row)
                return ["background-color: rgba(15, 23, 42, 0.65); color: #F8FAFC"] * len(row)

            st.dataframe(
                df_p_show.style.apply(style_player_day, axis=1),
                use_container_width=True,
                hide_index=True,
                height=520,
                column_config={
                    "Nom du joueur": st.column_config.TextColumn("Nom du joueur", width="medium"),
                    "Pos": st.column_config.TextColumn("Pos", width="small", alignment="center"),
                    "Statut": st.column_config.TextColumn("Statut", width="small", alignment="center"),
                    "Éq. LNH": st.column_config.TextColumn("LNH", width="small", alignment="center"),
                    "But": st.column_config.NumberColumn("But", width="small", alignment="center"),
                    "Assiste": st.column_config.NumberColumn("Assiste", width="small", alignment="center"),
                    "Point": st.column_config.NumberColumn("Point", width="small", alignment="center"),
                }
            )
        else:
            st.info("Aucun joueur trouvé pour cette équipe.")

    # Petit outil de vérification pour voir tous les pointeurs LNH du jour
    if live_skaters:
        with st.expander("🔍 Vérifier tous les pointeurs LNH du jour (et s'ils sont dans le pool)"):
            df_check = pd.DataFrame([
                {
                    "Joueur LNH": lp["display"],
                    "Équipe": lp["team"],
                    "But": lp["but"],
                    "Assiste": lp["assiste"],
                    "Point": lp["but"] + lp["assiste"],
                    "Trouvé dans PHDM": "✅ Oui" if lp["matched_in_pool"] else "— Libre / Non trouvé"
                }
                for lp in live_skaters
            ]).sort_values(by=["Point", "But"], ascending=[False, False]).reset_index(drop=True)
            st.dataframe(df_check, use_container_width=True, hide_index=True)

with tab_direct:
    render_tab_direct()
