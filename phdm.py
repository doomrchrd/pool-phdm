import streamlit as st
import pandas as pd
import requests
import unicodedata
import json
import os
import re
from datetime import datetime

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

def format_date_fr(val):
    if pd.isna(val): 
        return ""
    val_str = str(val).strip()
    if val_str.lower() in ["nan", "none", "", "nat", " "]: 
        return ""
    try:
        dt = pd.to_datetime(val)
        return f"{dt.day} {MOIS_FR.get(dt.month, '')} {dt.year}".strip()
    except Exception:
        # En cas d'exception, on supprime l'heure si elle existe
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
            if any(k in v for v in vals_str for k in ["equipe", "équipe", "date", "joueur", "choix", "ronde"]):
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

@st.cache_data(ttl=300)
def fetch_nhl_stats():
    headers = {"User-Agent": "Mozilla/5.0"}
    skaters, goalies = {}, {}
    try:
        url_sk = f"https://api.nhle.com/stats/rest/en/skater/summary?limit=-1&cayenneExp=seasonId={SAISON}%20and%20gameTypeId={GAME_TYPE}"
        r_sk = requests.get(url_sk, headers=headers, timeout=10)
        if r_sk.status_code == 200:
            for p in r_sk.json().get("data", []):
                nom_clean = clean_str(p.get("skaterFullName", ""))
                if nom_clean not in skaters: skaters[nom_clean] = []
                skaters[nom_clean].append({"pj": p.get("gamesPlayed", 0), "pts": p.get("points", 0), "team": p.get("teamAbbrevs", ""), "age": None, "posCode": p.get("positionCode", "")})
        url_sk_bio = f"https://api.nhle.com/stats/rest/en/skater/bios?limit=-1&cayenneExp=seasonId={SAISON}%20and%20gameTypeId={GAME_TYPE}"
        r_sk_bio = requests.get(url_sk_bio, headers=headers, timeout=10)
        if r_sk_bio.status_code == 200:
            for p in r_sk_bio.json().get("data", []):
                nom_clean = clean_str(p.get("skaterFullName", ""))
                if nom_clean in skaters and p.get("birthDate"):
                    try:
                        b_dt = datetime.strptime(p.get("birthDate"), "%Y-%m-%d").date()
                        today = datetime.now().date()
                        age = today.year - b_dt.year - ((today.month, today.day) < (b_dt.month, b_dt.day))
                        pos = p.get("positionCode", "")
                        for sk in skaters[nom_clean]:
                            if sk.get("posCode") == pos or not sk.get("posCode"): sk["age"] = age
                    except: pass
        url_g = f"https://api.nhle.com/stats/rest/en/goalie/summary?limit=-1&cayenneExp=seasonId={SAISON}%20and%20gameTypeId={GAME_TYPE}"
        r_g = requests.get(url_g, headers=headers, timeout=10)
        if r_g.status_code == 200:
            for g in r_g.json().get("data", []):
                v, otl, so = g.get("wins", 0), g.get("otLosses", 0), g.get("shutouts", 0)
                nom_clean = clean_str(g.get("goalieFullName", ""))
                if nom_clean not in goalies: goalies[nom_clean] = []
                goalies[nom_clean].append({"pj": g.get("gamesPlayed", 0), "pts": (v * 2) + (otl * 1) + (so * 1), "team": g.get("teamAbbrevs", ""), "age": None, "posCode": "G"})
        url_g_bio = f"https://api.nhle.com/stats/rest/en/goalie/bios?limit=-1&cayenneExp=seasonId={SAISON}%20and%20gameTypeId={GAME_TYPE}"
        r_g_bio = requests.get(url_g_bio, headers=headers, timeout=10)
        if r_g_bio.status_code == 200:
            for g in r_g_bio.json().get("data", []):
                nom_clean = clean_str(g.get("goalieFullName", ""))
                if nom_clean in goalies and g.get("birthDate"):
                    try:
                        b_dt = datetime.strptime(g.get("birthDate"), "%Y-%m-%d").date()
                        today = datetime.now().date()
                        age = today.year - b_dt.year - ((today.month, today.day) < (b_dt.month, b_dt.day))
                        for go in goalies[nom_clean]: go["age"] = age
                    except: pass
    except Exception as e: return skaters, goalies, f"Erreur API : {e}"
    total_pat = sum(len(v) for v in skaters.values())
    total_gard = sum(len(v) for v in goalies.values())
    return skaters, goalies, f"Connecté à la LNH : {total_pat} patineurs et {total_gard} gardiens."

nhl_skaters, nhl_goalies, api_status = fetch_nhl_stats()

SPECIAL_OVERRIDE = {"bowman braeden": {"pts": 7, "pj": 0, "team": "VGK", "age": 20}, "braeden bowman": {"pts": 7, "pj": 0, "team": "VGK", "age": 20}}
ALIASES = {
    "d astous charle ed": "charle edouard d astous", "d astous charle edouard": "charle edouard d astous",
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
    "lorenz reiger": "rieger lorenz", "gaudreau fred": "frederick gaudreau", "novak tom": "thomas novak",
    "smith william": "will smith", "boldy matthew": "matt boldy", "humphreys christia": "christian humphreys",
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
# 3. ÉQUIPES & DIRECTEURS GÉNÉRAUX
# =========================================================
POOLERS = {
    "Dallas Stars": "Dominic Richard", "Saint-Louis Blues": "Stéphane Moisan", "Vegas Golden Knights": "Alain Leclerc",
    "Chicago Black Hawks": "Carl Vigneault", "Seattle Kraken": "Stéphane Nadeau", "Buffalo Sabres": "Jean-Pierre Marois",
    "Philadelphia Flyers": "Serge Benoit", "UCKA MOCKBA": "Long Bui", "New-York Rangers": "Dany Chabot",
    "Vancouver Canucks": "Alex Couture", "Montréal Canadiens": "Gérard Blanchard"
}
SLUG_MAP = {"Dallas Stars": "dal", "Saint-Louis Blues": "stl", "Vegas Golden Knights": "vgk", "Chicago Black Hawks": "chi", "Seattle Kraken": "sea", "Buffalo Sabres": "buf", "Philadelphia Flyers": "phi", "UCKA MOCKBA": "uck", "New-York Rangers": "nyr", "Vancouver Canucks": "van", "Montréal Canadiens": "mtl"}
TEAM_ABBR_MAP = {"Dallas Stars": "DAL", "Saint-Louis Blues": "STL", "Vegas Golden Knights": "VGK", "Chicago Black Hawks": "CHI", "Seattle Kraken": "SEA", "Buffalo Sabres": "BUF", "Philadelphia Flyers": "PHI", "UCKA MOCKBA": "MCW", "New-York Rangers": "NYR", "Vancouver Canucks": "VAN", "Montréal Canadiens": "MTL"}
ABBR_TO_TEAM = {v: k for k, v in TEAM_ABBR_MAP.items()}

TEAM_LOGOS = {
    "Dallas Stars": "https://assets.nhle.com/logos/nhl/svg/DAL_light.svg", "Saint-Louis Blues": "https://assets.nhle.com/logos/nhl/svg/STL_light.svg",
    "Vegas Golden Knights": "https://assets.nhle.com/logos/nhl/svg/VGK_light.svg", "Chicago Black Hawks": "https://assets.nhle.com/logos/nhl/svg/CHI_light.svg",
    "Seattle Kraken": "https://assets.nhle.com/logos/nhl/svg/SEA_light.svg", "Buffalo Sabres": "https://assets.nhle.com/logos/nhl/svg/BUF_light.svg",
    "Philadelphia Flyers": "https://assets.nhle.com/logos/nhl/svg/PHI_light.svg", "UCKA MOCKBA": "https://upload.wikimedia.org/wikipedia/en/thumb/0/0f/HC_CSKA_Moscow_logo.svg/200px-HC_CSKA_Moscow_logo.svg.png",
    "New-York Rangers": "https://assets.nhle.com/logos/nhl/svg/NYR_light.svg", "Vancouver Canucks": "https://assets.nhle.com/logos/nhl/svg/VAN_light.svg",
    "Montréal Canadiens": "https://assets.nhle.com/logos/nhl/svg/MTL_light.svg"
}

def to_team_abbr(val):
    if not val: return None
    s = clean_str(str(val))
    s_upper = str(val).strip().upper()
    
    if s_upper in TEAM_ABBR_MAP.values(): return s_upper
    if s_upper in ["UCK", "UCKA", "MCW"]: return "MCW"
    
    words = s.split()
    # 1. Vancouver est testé EN PREMIER pour ne jamais entrer en conflit avec 'uck'
    if "van" in words or any(w in s for w in ["vancouver", "canuck", "canucks"]): return "VAN"
    # 2. UCKA MOCKBA officiel
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

TEAM_QUOTAS = {}

# =========================================================
# 4. GESTION DES FICHIERS (CSV & EXCEL MASTER)
# =========================================================
DATA_FILE = "phdm_state.json"
def get_file_path(filename):
    for c in [os.path.join(os.path.dirname(__file__), filename), filename, os.path.join("pages", filename)]:
        if os.path.exists(c): return c
    return filename

def match_team_name(val):
    abbr = to_team_abbr(val)
    if abbr and abbr in ABBR_TO_TEAM:
        return ABBR_TO_TEAM[abbr]
    return None

def init_rosters_from_csv():
    path = get_file_path("alignements_phdm.csv")
    if not os.path.exists(path): return False
    try: df_raw = pd.read_csv(path, sep=None, engine='python', encoding='utf-8-sig')
    except: df_raw = pd.read_csv(path, sep=';', encoding='latin1')

    col_map = {clean_str(c): c for c in df_raw.columns}
    col_equipe = col_map.get("equipe pool") or df_raw.columns[0]
    col_statut = col_map.get("statut") or df_raw.columns[1]
    col_pos = col_map.get("position") or df_raw.columns[2]
    col_joueur = col_map.get("joueurs") or df_raw.columns[3]
    col_team = col_map.get("t") or df_raw.columns[4]
    col_age = col_map.get("a") or df_raw.columns[5]
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
        for slug in SLUG_MAP.values(): st.session_state[f"{slug}_df_recrues"] = pd.DataFrame(columns=cols_master)
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
            nom, pos = val_b, str(r.iloc[2]).strip() if len(r) > 2 and pd.notna(r.iloc[2]) else ""
            annee, ronde = str(r.iloc[3]).strip() if len(r) > 3 and pd.notna(r.iloc[3]) else "", str(r.iloc[4]).strip() if len(r) > 4 and pd.notna(r.iloc[4]) else ""
            rang = str(r.iloc[5]).strip() if len(r) > 5 and pd.notna(r.iloc[5]) else ""
            cotes = f"{str(r.iloc[6]).strip()}-{str(r.iloc[7]).strip()}-{str(r.iloc[8]).strip()}".strip('-')
            if cotes == "--": cotes = ""
            club, ligue = str(r.iloc[9]).strip() if len(r) > 9 and pd.notna(r.iloc[9]) else "", str(r.iloc[10]).strip() if len(r) > 10 and pd.notna(r.iloc[10]) else ""
            proprio = str(r.iloc[16]).strip() if len(r) > 16 and pd.notna(r.iloc[16]) else ""
            if proprio.lower() == "nan": proprio = ""
            rows_master.append([len(rows_master)+1, nom, pos, annee, ronde, rang, cotes, club, ligue, current_nhl_team, proprio])
        elif not val_a.isdigit() and val_a.lower() not in ["nan", ""]:
            if "ronde" not in val_a.lower():
                if "canadien" in val_a.lower() and "montr" in val_a.lower(): current_nhl_team = "Montréal Canadiens"
                elif "draft" in val_a.lower(): current_nhl_team = "Draft NHL 2026"
                else: current_nhl_team = val_a
    df_master = pd.DataFrame(rows_master, columns=cols_master)
    st.session_state["df_all_recrues"] = df_master
    df_master["Standard_PHDM"] = df_master["Équipe PHDM"].apply(match_team_name)
    for team_name in POOLERS.keys():
        slug = SLUG_MAP[team_name]
        sub = df_master[df_master["Standard_PHDM"] == team_name].copy()
        sub["Pts"] = 0
        sub = sub.sort_values(by="Joueurs").reset_index(drop=True)
        sub["No"] = range(1, len(sub) + 1)
        st.session_state[f"{slug}_df_recrues"] = sub
    return True

def import_excel_transactions():
    path = get_file_path("gestion_phdm.xlsx")
    if not os.path.exists(path): return False
    try:
        df_t = pd.read_excel(path, sheet_name="Transactions")
        df_t = clean_excel_dataframe(df_t)
        st.session_state["trade_history"] = []
        for _, row in df_t.iterrows():
            if len(row) < 3: continue
            eq1_name = match_team_name(str(row.iloc[1]))
            eq2_name = match_team_name(str(row.iloc[2]))
            if not eq1_name or not eq2_name: continue
            slug1, slug2 = SLUG_MAP[eq1_name], SLUG_MAP[eq2_name]
            def parse_col(idx):
                val = str(row.iloc[idx])
                if pd.isna(row.iloc[idx]) or val.strip().lower() in ['nan', 'none', '']: return []
                return [i.strip() for i in val.split(',') if i.strip()]
            j1_list, c1_list = parse_col(3) if len(row) > 3 else [], parse_col(4) if len(row) > 4 else []
            j2_list, c2_list = parse_col(5) if len(row) > 5 else [], parse_col(6) if len(row) > 6 else []
            def move_players(j_list, s_src, s_dest):
                moved = []
                for nom in j_list:
                    nc = clean_str(nom)
                    for role in ["df_att", "df_def", "df_gard", "df_banc_att", "df_banc_def", "df_banc_gard", "df_recrues"]:
                        k_src = f"{s_src}_{role}"
                        if k_src in st.session_state and not st.session_state[k_src].empty:
                            df_s = st.session_state[k_src]
                            mask = df_s["Joueurs"].apply(clean_str) == nc
                            if mask.any():
                                row_j = df_s[mask].iloc[[0]].copy()
                                st.session_state[k_src] = df_s[~mask].reset_index(drop=True)
                                prefix = "banc_" if "banc" in role else ""
                                if "att" in role: k_d = f"{s_dest}_df_{prefix}att"
                                elif "def" in role: k_d = f"{s_dest}_df_{prefix}def"
                                elif "gard" in role: k_d = f"{s_dest}_df_{prefix}gard"
                                else: k_d = f"{s_dest}_df_recrues"
                                st.session_state[k_d] = pd.concat([st.session_state[k_d], row_j]).reset_index(drop=True)
                                moved.append(row_j.iloc[0]["Joueurs"])
                                break
                return moved
            m_j1, m_j2 = move_players(j1_list, slug1, slug2), move_players(j2_list, slug2, slug1)
            
            def parse_picks(c_list, a_src, a_dest):
                moved = []
                for c in c_list:
                    c_str = str(c).strip()
                    if not c_str: continue
                    m_yr = re.search(r'(202[5-9])', c_str)
                    yr = m_yr.group(1) if m_yr else "2026"
                    rd_num = None
                    m_rd = re.search(r'(?:ronde|rd|tour)\s*#?\s*(\d+)', c_str, re.IGNORECASE)
                    if m_rd: rd_num = int(m_rd.group(1))
                    else:
                        m_ord = re.search(r'(\d+)\s*(?:e|ere|ème|eme|th|nd|rd)\b', c_str, re.IGNORECASE)
                        if m_ord: rd_num = int(m_ord.group(1))
                        else:
                            m_pick_num = re.search(r'#\s*(\d+)', c_str)
                            if m_pick_num:
                                p_val = int(m_pick_num.group(1))
                                rd_num = ((p_val - 1) // 11) + 1
                            else:
                                m_gen = re.search(r'(\d+)', c_str)
                                if m_gen:
                                    val = int(m_gen.group(1))
                                    if 1 <= val <= 15: rd_num = val
                                    elif val in [31, 64]: rd_num = ((val - 1) // 11) + 1
                    
                    if rd_num and 1 <= rd_num <= 15:
                        rd = f"Ronde {rd_num}"
                        m_slot = re.search(r'\((.*?)\)', c_str)
                        slot = to_team_abbr(m_slot.group(1)) if m_slot else a_src
                        if ("31" in c_str or rd_num == 3) and a_src == "MCW": slot = "MCW"
                        if ("64" in c_str or rd_num == 6) and a_src == "MCW": slot = "MCW"
                        k_draft = f"df_draft_{yr}"
                        if k_draft in st.session_state:
                            df_d = st.session_state[k_draft]
                            if rd in df_d.index and slot in df_d.columns:
                                df_d.loc[rd, slot] = a_dest
                                moved.append(c_str)
                return moved

            m_c1 = parse_picks(c1_list, TEAM_ABBR_MAP[eq1_name], TEAM_ABBR_MAP[eq2_name])
            m_c2 = parse_picks(c2_list, TEAM_ABBR_MAP[eq2_name], TEAM_ABBR_MAP[eq1_name])
            
            # Formatage propre en français : 12 nov 2026
            date_clean = format_date_fr(row.iloc[0])
            date_str = date_clean if date_clean else "N/A"
            
            st.session_state["trade_history"].insert(0, {"Date": date_str, "Équipe A Name": eq1_name, "Équipe B Name": eq2_name, "A cédé": ", ".join(m_j1 + m_c1) or "Rien", "B a cédé": ", ".join(m_j2 + m_c2) or "Rien"})
    except Exception: return False
    return True

def save_state():
    state_data = {"rosters": {}, "drafts": {}, "trade_history": st.session_state.get("trade_history", [])}
    for slug in SLUG_MAP.values():
        state_data["rosters"][slug] = {}
        for role in ["att", "def", "gard", "banc_att", "banc_def", "banc_gard", "recrues"]:
            if f"{slug}_df_{role}" in st.session_state:
                state_data["rosters"][slug][role] = st.session_state[f"{slug}_df_{role}"].to_dict(orient="records")
    for annee in ["2026", "2027", "2028"]:
        if f"df_draft_{annee}" in st.session_state:
            state_data["drafts"][annee] = st.session_state[f"df_draft_{annee}"].to_dict(orient="index")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state_data, f, ensure_ascii=False, indent=4)

def load_state():
    if not os.path.exists(DATA_FILE): return False
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f: state_data = json.load(f)
        for slug, roles in state_data.get("rosters", {}).items():
            for role, records in roles.items(): st.session_state[f"{slug}_df_{role}"] = pd.DataFrame(records)
            st.session_state[f"{slug}_init"] = True
        for annee, records in state_data.get("drafts", {}).items(): st.session_state[f"df_draft_{annee}"] = pd.DataFrame.from_dict(records, orient="index")
        st.session_state["trade_history"] = state_data.get("trade_history", [])
        return True
    except: return False

def enforce_roster_limits(slug):
    quotas = TEAM_QUOTAS.get(slug, {"att": 25, "def": 11, "gard": 4})
    for pos, max_partants in [("att", quotas["att"]), ("def", quotas["def"]), ("gard", quotas["gard"])]:
        df_p = st.session_state.get(f"{slug}_df_{pos}")
        df_b = st.session_state.get(f"{slug}_df_banc_{pos}")
        if df_p is not None and df_b is not None and len(df_p) > max_partants:
            df_p = df_p.sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
            demoted = df_p.iloc[max_partants:].copy()
            df_p = df_p.iloc[:max_partants].copy()
            df_b = pd.concat([df_b, demoted]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
            df_p["No"] = range(1, len(df_p) + 1)
            df_b["No"] = range(1, len(df_b) + 1)
            st.session_state[f"{slug}_df_{pos}"] = df_p
            st.session_state[f"{slug}_df_banc_{pos}"] = df_b

data_loaded = load_state()
if not data_loaded or (f"{SLUG_MAP['Dallas Stars']}_df_att" not in st.session_state or st.session_state[f"{SLUG_MAP['Dallas Stars']}_df_att"].empty):
    init_rosters_from_csv()
    init_recrues_from_csv()
    abbrs = list(TEAM_ABBR_MAP.values())
    for annee in ["2026", "2027", "2028"]:
        if f"df_draft_{annee}" not in st.session_state:
            st.session_state[f"df_draft_{annee}"] = pd.DataFrame({t: [t]*15 for t in abbrs}, index=[f"Ronde {i}" for i in range(1, 16)])

if "df_all_recrues" not in st.session_state: init_recrues_from_csv()
for slug in SLUG_MAP.values(): enforce_roster_limits(slug)

# =========================================================
# 5. INITIALISATION DU THÈME DYNAMIQUE DE L'ÉQUIPE (DARK MODE)
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
        "accent": "#E2E8F0",
        "btn_grad": "linear-gradient(135deg, #10B981, #047857)",
        "card_bg": "rgba(15, 30, 22, 0.6)"
    },
    "Montréal Canadiens": {
        "bg_top": "#2E0F15", "bg_bottom": "#0B1121",
        "primary": "#EF4444", "primary_glow": "rgba(239,68,68,0.4)",
        "accent": "#60A5FA",
        "btn_grad": "linear-gradient(135deg, #EF4444, #1E3A8A)",
        "card_bg": "rgba(30, 15, 20, 0.6)"
    },
    "Saint-Louis Blues": {
        "bg_top": "#0F172A", "bg_bottom": "#020617",
        "primary": "#3B82F6", "primary_glow": "rgba(59,130,246,0.4)",
        "accent": "#FACC15",
        "btn_grad": "linear-gradient(135deg, #3B82F6, #1D4ED8)",
        "card_bg": "rgba(15, 25, 45, 0.6)"
    },
    "Vegas Golden Knights": {
        "bg_top": "#272215", "bg_bottom": "#111315",
        "primary": "#D4AF37", "primary_glow": "rgba(212,175,55,0.4)",
        "accent": "#94A3B8",
        "btn_grad": "linear-gradient(135deg, #D4AF37, #856014)",
        "card_bg": "rgba(35, 30, 20, 0.6)"
    },
    "Chicago Black Hawks": {
        "bg_top": "#2A0A0A", "bg_bottom": "#0A0A0A",
        "primary": "#EF4444", "primary_glow": "rgba(239,68,68,0.4)",
        "accent": "#F8FAFC",
        "btn_grad": "linear-gradient(135deg, #EF4444, #991B1B)",
        "card_bg": "rgba(30, 10, 10, 0.6)"
    },
    "Seattle Kraken": {
        "bg_top": "#081E2D", "bg_bottom": "#030B14",
        "primary": "#67E8F9", "primary_glow": "rgba(103,232,249,0.4)",
        "accent": "#F97316",
        "btn_grad": "linear-gradient(135deg, #06B6D4, #0369A1)",
        "card_bg": "rgba(10, 25, 40, 0.6)"
    },
    "Buffalo Sabres": {
        "bg_top": "#0B1B36", "bg_bottom": "#050A14",
        "primary": "#FACC15", "primary_glow": "rgba(250,204,21,0.4)",
        "accent": "#60A5FA",
        "btn_grad": "linear-gradient(135deg, #3B82F6, #FACC15)",
        "card_bg": "rgba(15, 25, 45, 0.6)"
    },
    "Philadelphia Flyers": {
        "bg_top": "#2D1608", "bg_bottom": "#0A0502",
        "primary": "#F97316", "primary_glow": "rgba(249,115,22,0.4)",
        "accent": "#F8FAFC",
        "btn_grad": "linear-gradient(135deg, #F97316, #111111)",
        "card_bg": "rgba(35, 15, 5, 0.6)"
    },
    "UCKA MOCKBA": {
        "bg_top": "#2A0A10", "bg_bottom": "#0A1020",
        "primary": "#E11D48", "primary_glow": "rgba(225,29,72,0.4)",
        "accent": "#FBBF24",
        "btn_grad": "linear-gradient(135deg, #E11D48, #881337)",
        "card_bg": "rgba(30, 10, 15, 0.6)"
    },
    "New-York Rangers": {
        "bg_top": "#0A1930", "bg_bottom": "#050814",
        "primary": "#3B82F6", "primary_glow": "rgba(59,130,246,0.4)",
        "accent": "#EF4444",
        "btn_grad": "linear-gradient(135deg, #3B82F6, #1E3A8A)",
        "card_bg": "rgba(10, 20, 40, 0.6)"
    },
    "Vancouver Canucks": {
        "bg_top": "#0A1C2A", "bg_bottom": "#051012",
        "primary": "#10B981", "primary_glow": "rgba(16,185,129,0.4)",
        "accent": "#3B82F6",
        "btn_grad": "linear-gradient(135deg, #3B82F6, #10B981)",
        "card_bg": "rgba(10, 25, 30, 0.6)"
    }
}

DEFAULT_THEME = {
    "bg_top": "#1E293B", "bg_bottom": "#0B1120",
    "primary": "#38BDF8", "primary_glow": "rgba(56,189,248,0.4)",
    "accent": "#94A3B8",
    "btn_grad": "linear-gradient(135deg, #0284C7, #0369A1)",
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

tab_equipes, tab_transactions, tab_reglements, tab_repechage, tab_recrues = st.tabs([
    "🏒 ALIGNEMENTS & ÉQUIPES", "🔁 CENTRE DES TRANSACTIONS", "📜 RÈGLEMENTS DU POOL", "🎟️ CHOIX DE REPÊCHAGE", "🌱 BANQUE DES RECRUES"
])

# =========================================================
# ONGLET 1 : ÉQUIPES & PRIX
# =========================================================
with tab_equipes:
    limites = TEAM_QUOTAS.get(team_slug, {"att": 25, "def": 11, "gard": 4})

    top_gauche, top_droite = st.columns([0.95, 2.05], vertical_alignment="top")
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
        st.markdown("<h4 style='margin:0 0 10px 0; text-align:center; color: #94A3B8; font-weight: 800; font-size: 1.15rem; letter-spacing: 1px;'>💰 PRIX OFFICIELS & CLASSEMENT COMPLET (PHDM)</h4>", unsafe_allow_html=True)
        cl1, cl2, cl3, cl4 = st.columns(4)
        card_style = "background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); min-height: 360px;"
        
        with cl1:
            st.markdown(f"""
            <div style='{card_style}'>
                <div style='font-weight: 900; color: #F59E0B; font-size: 1.02rem; border-bottom: 2px solid #F59E0B; padding-bottom: 6px; text-align: center; letter-spacing: 0.5px;'>
                    1. GAGNANT DU POOL
                </div>
                <div style='margin-top: 10px; font-size: 0.92rem; line-height: 1.5;'>
                    {build_ranking_html(rank_general, 'total', 12)}
                </div>
            </div>
            """, unsafe_allow_html=True)

        with cl2:
            st.markdown(f"""
            <div style='{card_style}'>
                <div style='font-weight: 900; color: #EF4444; font-size: 1.02rem; border-bottom: 2px solid #EF4444; padding-bottom: 6px; text-align: center; letter-spacing: 0.5px;'>
                    2. MEILLEURS ATT
                </div>
                <div style='margin-top: 10px; font-size: 0.92rem; line-height: 1.5;'>
                    {build_ranking_html(rank_att, 'att', 12)}
                    <span style='color: #94A3B8; font-size: 0.82rem;'><br>❌ 1er Général exclu</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

        with cl3:
            st.markdown(f"""
            <div style='{card_style}'>
                <div style='font-weight: 900; color: #38BDF8; font-size: 1.02rem; border-bottom: 2px solid #38BDF8; padding-bottom: 6px; text-align: center; letter-spacing: 0.5px;'>
                    3. MEILLEURS DÉF
                </div>
                <div style='margin-top: 10px; font-size: 0.92rem; line-height: 1.5;'>
                    {build_ranking_html(rank_def, 'def', 12)}
                    <span style='color: #94A3B8; font-size: 0.82rem;'><br>❌ 1er Général exclu</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

        with cl4:
            st.markdown(f"""
            <div style='{card_style}'>
                <div style='font-weight: 900; color: #10B981; font-size: 1.02rem; border-bottom: 2px solid #10B981; padding-bottom: 6px; text-align: center; letter-spacing: 0.5px;'>
                    4. MEILLEURS GARD
                </div>
                <div style='margin-top: 10px; font-size: 0.92rem; line-height: 1.5;'>
                    {build_ranking_html(rank_gard, 'gard', 12)}
                    <span style='color: #94A3B8; font-size: 0.82rem;'><br>❌ 1er Général exclu</span>
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
    
    # 1. Vérification stricte des barèmes par position
    stat_att = (19 <= len(df_att) <= 25) and (nb_att_tot <= 80)
    stat_def = (7 <= len(df_def) <= 11) and (nb_def_tot <= 30)
    stat_gard = (2 <= len(df_gard) <= 4) and (nb_gard_tot <= 18)
    
    # 2. Total Concession conforme UNIQUEMENT si toutes les positions sont conformes
    stat_globale = (nb_tot_concession <= 128) and stat_att and stat_def and stat_gard

    # 3. Fonction de style pour les alertes rouges
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
            <div style="font-size: 1rem; margin-top: 4px; color:#F8FAFC;"><b>Partants :</b> {len(df_att)} / [19-25]</div>
            <div style="font-size: 0.85rem; color: #94A3B8;"><b>Total Concession :</b> {nb_att_tot} / 80 max</div>
            <div style="font-size: 0.8rem; font-weight: 800; margin-top: 4px; color: {'#34D399' if stat_att else '#EF4444'}; text-decoration: {'none' if stat_att else 'underline'};">{'✅ Conforme' if stat_att else '⚠️ Quota dépassé'}</div>
        </div>
        """, unsafe_allow_html=True)

    with q2:
        st.markdown(f"""
        <div style="{get_card_style(stat_def)}">
            <div style="font-size: 0.85rem; font-weight: 900; color:{'#34D399' if stat_def else '#FCA5A5'};">🛡️ DÉFENSEURS {'✅' if stat_def else '⚠️'}</div>
            <div style="font-size: 1rem; margin-top: 4px; color:#F8FAFC;"><b>Partants :</b> {len(df_def)} / [7-11]</div>
            <div style="font-size: 0.85rem; color: #94A3B8;"><b>Total Concession :</b> {nb_def_tot} / 30 max</div>
            <div style="font-size: 0.8rem; font-weight: 800; margin-top: 4px; color: {'#34D399' if stat_def else '#EF4444'}; text-decoration: {'none' if stat_def else 'underline'};">{'✅ Conforme' if stat_def else '⚠️ Quota dépassé'}</div>
        </div>
        """, unsafe_allow_html=True)

    with q3:
        st.markdown(f"""
        <div style="{get_card_style(stat_gard)}">
            <div style="font-size: 0.85rem; font-weight: 900; color:{'#34D399' if stat_gard else '#FCA5A5'};">🥅 GARDIENS {'✅' if stat_gard else '⚠️'}</div>
            <div style="font-size: 1rem; margin-top: 4px; color:#F8FAFC;"><b>Partants :</b> {len(df_gard)} / [2-4]</div>
            <div style="font-size: 0.85rem; color: #94A3B8;"><b>Total Concession :</b> {nb_gard_tot} / 18 max</div>
            <div style="font-size: 0.8rem; font-weight: 800; margin-top: 4px; color: {'#34D399' if stat_gard else '#EF4444'}; text-decoration: {'none' if stat_gard else 'underline'};">{'✅ Conforme' if stat_gard else '⚠️ Quota dépassé'}</div>
        </div>
        """, unsafe_allow_html=True)

    with q4:
        st.markdown(f"""
        <div style="{get_card_style(stat_globale)}">
            <div style="font-size: 0.85rem; font-weight: 900; color:{'#34D399' if stat_globale else '#FCA5A5'};">🏢 TOTAL CONCESSION {'✅' if stat_globale else '⚠️'}</div>
            <div style="font-size: 1rem; margin-top: 4px; color:#F8FAFC;"><b>Effectif :</b> {nb_tot_concession} / 128 max</div>
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

    # =========================================================
    # BARRE D'ACTION (VERROUILLÉE POUR LES VISITEURS)
    # =========================================================
    c1, c2, c3 = st.columns([4, 1.2, 1.2])
    with c1: st.info(f"🟢 **Statut API LNH** : {api_status}")
    
    with c2:
        if is_admin:
            if st.button("🔄 ACTUALISER API", use_container_width=True):
                st.cache_data.clear()
                for slg in SLUG_MAP.values():
                    for role, pos_cat in [("att", "ATT"), ("def", "DEF"), ("gard", "GARD"), ("banc_att", "ATT"), ("banc_def", "DEF"), ("banc_gard", "GARD")]:
                        if f"{slg}_df_{role}" in st.session_state:
                            cur_df = st.session_state[f"{slg}_df_{role}"]
                            for idx_p, row_p in cur_df.iterrows():
                                t_r, age_r, pj_r, pts_r = get_player_live_stats(row_p["Joueurs"], row_p["T"], row_p.get("A", 0), pos_cat)
                                cur_df.at[idx_p, "T"] = t_r; cur_df.at[idx_p, "A"] = age_r; cur_df.at[idx_p, "PJ"] = pj_r; cur_df.at[idx_p, "Pts"] = pts_r
                            st.session_state[f"{slg}_df_{role}"] = cur_df.sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                for slg in SLUG_MAP.values(): enforce_roster_limits(slg)
                save_state()
                st.rerun()
        else:
            st.button("🔄 ACTUALISER API", use_container_width=True, disabled=True, help="🔒 Réservé au commissaire.")
            
    with c3:
        if is_admin:
            if st.button("📥 RECHARGER BASE", use_container_width=True, type="primary"):
                init_rosters_from_csv()
                init_recrues_from_csv()
                abbrs = list(TEAM_ABBR_MAP.values())
                for annee in ["2026", "2027", "2028"]:
                    st.session_state[f"df_draft_{annee}"] = pd.DataFrame({t: [t]*15 for t in abbrs}, index=[f"Ronde {i}" for i in range(1, 16)])
                import_excel_transactions()
                for slg in SLUG_MAP.values(): enforce_roster_limits(slg)
                save_state()
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
                save_state()
                st.rerun()
        else:
            st.button("⚡ **APPLIQUER LES PERMUTATIONS (1 POUR 1)**", use_container_width=True, disabled=True, help="🔒 Réservé au commissaire.")

    # =========================================================
    # PERMUTATION MANUELLE (MASQUÉE POUR LES VISITEURS)
    # =========================================================
    if is_admin:
        with st.expander("🛠️ PERMUTATION MANUELLE (Monter, descendre ou combler)"):
            pos_choix = st.radio("Position à modifier :", ["Attaquants", "Défenseurs", "Gardiens"], horizontal=True, key=f"man_pos_{team_slug}")
            min_att, min_def, min_gard = 19, 7, 2
            
            if pos_choix == "Attaquants": 
                df_p, df_b, k_p, k_b = df_att, df_banc_att, f"{team_slug}_df_att", f"{team_slug}_df_banc_att"
                min_p, max_p = min(min_att, limites["att"]), limites["att"]
            elif pos_choix == "Défenseurs": 
                df_p, df_b, k_p, k_b = df_def, df_banc_def, f"{team_slug}_df_def", f"{team_slug}_df_banc_def"
                min_p, max_p = min(min_def, limites["def"]), limites["def"]
            else: 
                df_p, df_b, k_p, k_b = df_gard, df_banc_gard, f"{team_slug}_df_gard", f"{team_slug}_df_banc_gard"
                min_p, max_p = min(min_gard, limites["gard"]), limites["gard"]
                
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
                        save_state(); st.rerun()
                    elif b_sel.startswith("[Laisser") and not p_sel.startswith("[Combler"):
                        row_p = df_p[df_p["Joueurs"] == p_sel].copy()
                        new_p = df_p[df_p["Joueurs"] != p_sel].sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_b = pd.concat([df_b, row_p]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_p["No"], new_b["No"] = range(1, len(new_p) + 1), range(1, len(new_b) + 1)
                        st.session_state[k_p], st.session_state[k_b] = new_p, new_b
                        save_state(); st.rerun()
                    elif not p_sel.startswith("[Combler") and not b_sel.startswith("[Laisser"):
                        row_p = df_p[df_p["Joueurs"] == p_sel].copy()
                        row_b = df_b[df_b["Joueurs"] == b_sel].copy()
                        new_p = pd.concat([df_p[df_p["Joueurs"] != p_sel], row_b]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_b = pd.concat([df_b[df_b["Joueurs"] != b_sel], row_p]).sort_values(by=["Pts", "PJ"], ascending=[False, False]).reset_index(drop=True)
                        new_p["No"], new_b["No"] = range(1, len(new_p) + 1), range(1, len(new_b) + 1)
                        st.session_state[k_p], st.session_state[k_b] = new_p, new_b
                        save_state(); st.rerun()
    else:
        st.info("ℹ️ **Mode Consultation (Lecture seule)** : L'alignement et les permutations sont réservés au commissaire.")

    def fill_empty(df, m):
        if len(df) < m: return pd.concat([df, pd.DataFrame([{"No": i, "Joueurs": "[Carreau Libre]", "T": "—", "A": 0, "EXP": 0, "PJ": 0, "Pts": 0} for i in range(len(df)+1, m+1)])], ignore_index=True)
        return df.copy()

    df_att_disp, df_def_disp, df_gard_disp = fill_empty(df_att, limites["att"]), fill_empty(df_def, limites["def"]), fill_empty(df_gard, limites["gard"])

    def color_part(r, rl): return ['background-color: transparent; color: #475569; font-style: italic']*len(r) if r["Joueurs"]=="[Carreau Libre]" else (['background-color: #7F1D1D; color: #FECACA; font-weight: bold']*len(r) if afficher_couleurs and r["Joueurs"] in rl else ['background-color: rgba(15,23,42,0.6); color: #F8FAFC']*len(r))
    def color_banc(r, gl): return ['background-color: #064E3B; color: #D1FAE5; font-weight: bold']*len(r) if afficher_couleurs and r["Joueurs"] in gl else (['background-color: rgba(30,41,59,0.4); color: #94A3B8']*len(r))

    COL_CONF = {"No": st.column_config.NumberColumn("No", alignment="center"), "Joueurs": st.column_config.TextColumn("Joueurs", alignment="left"), "T": st.column_config.TextColumn("T", alignment="center"), "A": st.column_config.NumberColumn("A", alignment="center"), "EXP": st.column_config.NumberColumn("EXP", alignment="center"), "PJ": st.column_config.NumberColumn("PJ", alignment="center"), "Pts": st.column_config.NumberColumn("Pts", alignment="center")}

    co1, co2, co3 = st.columns([1, 1, 1])
    with co1:
        st.subheader(f"🏒 Attaquants Partants ({len(df_att)} / {limites['att']})")
        st.dataframe(df_att_disp.style.apply(color_part, rl=r_att, axis=1), hide_index=True, use_container_width=True, height=540, column_config=COL_CONF)
        st.markdown(f"<div style='color:{t['primary']}; font-weight:900;'>Total Attaquants : {int(df_att['Pts'].sum())} pts</div>", unsafe_allow_html=True)
        st.divider()
        st.subheader(f"🛡️ Défenseurs Partants ({len(df_def)} / {limites['def']})")
        st.dataframe(df_def_disp.style.apply(color_part, rl=r_def, axis=1), hide_index=True, use_container_width=True, height=280, column_config=COL_CONF)
        st.markdown(f"<div style='color:{t['primary']}; font-weight:900;'>Total Défenseurs : {int(df_def['Pts'].sum())} pts</div>", unsafe_allow_html=True)
        st.divider()
        st.subheader(f"🥅 Gardiens Partants ({len(df_gard)} / {limites['gard']})")
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
# ONGLET 2 : CENTRE DES TRANSACTIONS (EXCEL PROTÉGÉ)
# =========================================================
with tab_transactions:
    st.markdown(f"<h1 style='color:{t['primary']};'>🔄 CENTRE OFFICIEL DES TRANSACTIONS</h1>", unsafe_allow_html=True)
    path_excel = get_file_path("gestion_phdm.xlsx")
    if os.path.exists(path_excel):
        try:
            df_t_show = pd.read_excel(path_excel, sheet_name="Transactions")
            df_t_show = clean_excel_dataframe(df_t_show)

            # Identification et conversion du format de date (ex: 12 nov 2026 sans heure)
            cols_date = [c for c in df_t_show.columns if "date" in str(c).lower()]
            col_target = cols_date[0] if cols_date else (df_t_show.columns[0] if len(df_t_show.columns) > 0 else None)
            
            if col_target:
                df_t_show[col_target] = df_t_show[col_target].apply(format_date_fr)

            styler_t = df_t_show.style
            try: styler_t = styler_t.map(color_empty_cells)
            except AttributeError: styler_t = styler_t.applymap(color_empty_cells)
            st.dataframe(styler_t, use_container_width=True, hide_index=True, height=680)
        except PermissionError:
            st.error("⚠️ Le fichier Excel est actuellement ouvert ! Fermez Microsoft Excel puis rafraîchissez la page.")
        except Exception as e:
            st.warning(f"Impossible de lire l'onglet 'Transactions' : {e}")
    else: 
        st.info("Fichier `gestion_phdm.xlsx` introuvable.")

# =========================================================
# ONGLET 3 : RÈGLEMENTS OFFICIELS DU POOL (MISE À JOUR 2024)
# =========================================================
with tab_reglements:
    st.markdown(f"""
    <div style='background: linear-gradient(135deg, #1E1B4B, #0F172A); border: 2px solid {t['primary']}; padding: 20px; border-radius: 14px; margin-bottom: 25px; text-align: center; box-shadow: 0 4px 20px rgba(0,0,0,0.5);'>
        <h1 style='margin:0; font-weight:900; color: #FFFFFF; font-size: 2.3rem; letter-spacing: 2px;'>📜 RÈGLEMENTS DU PHDM-2</h1>
        <div style='background: #EA580C; color: #FFFFFF; font-weight: 900; display: inline-block; padding: 6px 20px; border-radius: 20px; font-size: 1.1rem; margin-top: 10px; letter-spacing: 1px;'>
            MISE À JOUR : 22 NOVEMBRE 2024
        </div>
    </div>
    """, unsafe_allow_html=True)

    with st.expander("⚖️ ARTICLES FONDAMENTAUX (ART. 001 À 003) — DIRECTION ET RÈGLEMENT BETTMAN", expanded=True):
        st.markdown("""
        * **ART 001** : Le président et le statisticien ont des **pouvoirs exceptionnels pour l'orientation des règles du pool** qu'ils peuvent utiliser à leur guise pour assurer une fluidité dans les opérations *(règlement Bettman)*. Donc, **IMPORTANT**, les règles du pool sont un cadre et non une fin. Le jugement et le gros bon sens du **STATISTICIEN ET DU PRÉSIDENT AURONT TOUJOURS PRÉSÉANCE SUR TOUS LES ARTICLES**. C'est le pouvoir du règlement Bettman.
        * **ART 002** : Si vous avez une plainte, **celui qui prend les plaintes est le président et le seul moyen de communication permise sera le cellulaire ou par courriel en privé**. Si vous vous sentez lésé, et que vous exprimez votre frustration par courriel en mettant un autre DG, automatiquement **vous perdez votre cause sans discussion**. Si vous insistez à contester en mettant tout le monde en CC, le DG en question **PERDRA SON 1ER CHOIX AU PROCHAIN REPÊCHAGE**.
        * **ART 003** : Il n'y a **pas de vote démocratique** pour changer le président OU le statisticien. Ceux-ci sont **nommés à vie**.
        """)

    col_r1, col_r2 = st.columns(2)

    with col_r1:
        with st.expander("💰 SECTION 1 — COÛT DE LA FRANCHISE ET PRIX DISTRIBUÉS", expanded=True):
            st.markdown("""
            * **Art. 101** : Le coût d'une franchise par année est de **50$**. Le paiement doit être acquitté au moment de la première rencontre en début de saison.
            * **Art. 102** : Les prix sont distribués à la fin de la saison de la façon suivante :
              * 🥇 **1- GAGNANT DU POOL :** 345$
              * 🏒 **2- GAGNANT POSITION ATTAQUANTS :** 50$
              * 🛡️ **3- GAGNANT DES DÉFENSEURS :** 50$
              * 🥅 **4- GAGNANT DES GARDIENS :** 50$
              * 📊 **6- STATISTICIEN :** 55$
            * **Art 103** : Le gagnant du pool **ne peut pas gagner les positions de l'Art. 102**.
            * **Art 104** : SI UN PARTICIPANT DÉMISSIONNE DURANT LA SAISON, **AUCUN REMBOURSEMENT NE SERA FAIT** VU QUE LA SOMME A DÉJÀ ÉTÉ ENGAGÉE.
            """)

        with st.expander("📅 SECTION 2 — LISTE DES RENCONTRES DU POOL PHDM-2"):
            st.markdown("""
            * **Art. 201** : Lors des réunions, aucune décision ou vote ne peuvent être pris sans le quorum. Pour qu'il y ait quorum, il doit y avoir **50% + 1** des directeurs généraux présents à la rencontre.
            * **Art. 202** : Une rencontre minimale est prévue lors d'une année (draft amateur), rencontre physique ou virtuelle.
            * **Art. 203** : Dans la mesure du possible, les rencontres se déroulent sur Messenger par Facebook.
            * **Art. 204** : Lors de la rencontre du repêchage amateur, **ON DÉCIDE DES CHANGEMENTS DE RÈGLEMENTS**.
            """)

        with st.expander("👥 SECTION 3 — RÈGLES PRÉCISES POUR LES ALIGNEMENTS & QUOTAS", expanded=True):
            st.markdown("""
            * **Art. 301** : Une concession doit se composer d'un **maximum de 80 attaquants, 30 défenseurs et 18 gardiens (128 joueurs au total)**. Un directeur général qui ne respecte pas ce quota doit obligatoirement libérer un joueur dans les secondes qui suivent le repêchage d'un nouveau joueur. Un DG peut faire une offre sur-le-champ pour le joueur libéré, sinon il est scratché. Si nous voulons échanger des joueurs que nous allons libérer, il faut le faire avant le repêchage.
            * **Art 302** : Un alignement partant en début d'année est constitué de :
              * **A)** 22 attaquants
              * **B)** 9 défenseurs
              * **C)** 3 gardiens
            * **Art 303** : Le nombre de joueurs dans un alignement peut être différent de celui du départ à la condition qu'il respecte les barèmes suivants :
              * **A)** Un Minimum de 19 et un Maximum de 25 attaquants
              * **B)** Un Minimum de 7 et un Maximum de 11 défenseurs
              * **C)** Un Minimum de 2 et un Maximum de 4 gardiens
            * **Art 304** : Lorsqu'il y a un joueur qui peut jouer autant comme défenseur qu'attaquant, l'ensemble des DGs, à la majorité, décident à la première rencontre en octobre la position du joueur. Le joueur en question devra toutefois rester à cette position toute la saison.
            * **ART 305** : En début de saison, vous pouvez mettre tout genre de joueur selon leurs statuts, **SAUF qu'après les 30 matchs, on doit obligatoirement mettre les meilleurs sur le partant**.
            """)

        with st.expander("📊 SECTION 4 — CALCUL OFFICIEL DES POINTS"):
            st.markdown("""
            * **Art. 401** : Le calcul des points pour les attaquants et les défenseurs se fait comme suit :
              * **A)** Un but équivaut à un point (1 pt)
              * **B)** Une mention d'aide équivaut à un point (1 pt)
            * **Art 402** : Le calcul des points pour les gardiens se fait comme suit :
              * **C)** Une victoire donne **2 points**
              * **D)** Une nulle (ou défaite en prolongation / fusillade) donne **1 point**
              * **E)** Un blanchissage donne **1 point** supplémentaire
            * **Art 403** : Les points de chaque catégorie sont cumulés pour donner un grand total. Le gagnant du pool ne peut pas gagner les positions.
            * **Art 404** : Le classement officiel est disponible et suivi en direct sur le site internet du pool.
            """)

        with st.expander("🚪 SECTION 5 — LIBÉRATION DE JOUEURS"):
            st.markdown("""
            * **Art. 501** : Un directeur général peut libérer en tout temps un joueur de sa concession. Il a la responsabilité d'en informer les autres directeurs généraux.
            * **Art. 502** : Lors du repêchage, si un directeur général atteint la limite permise de joueurs par l'article 301, il doit obligatoirement libérer un joueur sur-le-champ. Par contre, il est permis d'être au-dessus de la limite de 128 joueurs durant l'été, **mais l'équipe doit être légale le 15 septembre de chaque année**.
            """)

    with col_r2:
        with st.expander("🔁 SECTION 6 — TRANSACTIONS, COLLUSION & RÈGLE DU 7/10 ANS", expanded=True):
            st.markdown("""
            * **Art. 601** : Une transaction peut se faire durant toute l'année sauf entre la date de la fin des transactions (Trade Deadline) et la fin de la saison.
            * **Art. 602** : Lorsqu'une transaction est conclue, elle doit être annoncée aux autres DG le lendemain au maximum, par les DG concernés.
            * **Art 603** : Une transaction impliquant un joueur blessé n'est jamais annulée.
            * **Art 604** : **AUCUNE TRANSACTION CONCLUE (QUELLE QUE SOIT LA TRANSACTION) NE DOIT AVOIR AUCUN COMMENTAIRE SUR LES RÉSEAUX SOCIAUX SINON RISQUE D'ÊTRE EXCLU (EXPULSION DU POOL).**
            * **Art 605** : Un joueur transigé ne peut pas revenir dans son équipe d'origine dans la même année, mais peut y revenir dans l'entre-saison.
            * **Art 606** : Une transaction illégale sera sanctionnée durement (exclusion du pool CAR S'IL Y A COLLUSION) si elle pose préjudice à un autre DG (se référer à l'Art 604).
            * **Art 606.a** : S'il y a collusion, le président du pool et son adjoint feront une analyse et décideront de la marche à suivre.
            * **Art 606.b** : Si le président et son adjoint font de la collusion, la transaction sera analysée par 2 DGs qui sont en bas de classement.
            * **Art 607** : **Un joueur ne peut appartenir à une équipe plus de 7 années consécutives sinon il devra être soumis au repêchage, sauf 10 ans pour les gardiens.** Une année d'ancienneté est accumulée pour chaque joueur à la fin de l'année. **6 joueurs par concession peuvent s'abstenir de ce règlement (protection spéciale) :**
              * **A :** 3 attaquants
              * **B :** 2 défenseurs
              * **C :** 1 gardien
            * **Art 608** : À la date limite des transactions, essayez d'être présent sur votre ordinateur pour ne pas nuire à un DG qui essaie de gagner le pool ou à ses poursuivants.
            * **Art 609** : Les choix aux repêchages durant la saison ne peuvent être transigés que sur **deux années ouvrables**, mais la troisième année sera ouverte de la fin de saison jusqu'au draft et après le draft on revient à 2 années ouvrables.
            * **Art 610** : Un joueur qui est sur le banc ne peut pas être monté sur le partant s'il a moins de points que le joueur partant, et surtout s'il y a transaction.
            * **Art 611** : Pendant la saison morte, si un club est au-dessus de la limite permise dans une des positions, il peut faire des transactions pour devenir légal ou simplement les balloter. Le concurrent doit être légal le **15 septembre de chaque année**.
            """)

        with st.expander("🎟️ SECTION 7 — REPÊCHAGE ANNUEL DES ESPOIRS", expanded=True):
            st.markdown("""
            * **Art. 701** : Dans la mesure du possible, le repêchage doit se dérouler avant le repêchage de la LNH de chaque année.
            * **Art. 702** : Chaque repêchage comprend **15 rondes**. Évidemment, un directeur général peut, par le biais de transactions, choisir plus de 15 joueurs.
            * **Art 703** : Si un DG choisit un joueur qui appartient déjà à un autre DG, il peut faire un autre choix dès que l'erreur est constatée. Si c'est durant le draft, il choisit un autre joueur sur-le-champ. Si on s'aperçoit de l'erreur après le draft, le DG choisit un joueur disponible à ce moment.
            * **Art 704** : *(SI IL Y A ABSENCE)* Lors d'un repêchage, en circonstances exceptionnelles, un DG peut faire une liste papier et demander à un autre DG de repêcher pour lui. Si le DG ne fait pas de liste et ne communique pas ses choix (FaceTime ou autre), **il ne peut pas repêcher et perd ses choix**.
            * **Art 705** : Pour les choix aux repêchages, à chaque draft de chaque année, on ouvre le tableau officiel des choix.
            * **Art 706** : Le repêchage se tiendra au mois de **mai ou juin de chaque année**.
            """)

        with st.expander("🗳️ SECTION 8 — MODIFICATIONS AUX RÈGLEMENTS"):
            st.markdown("""
            * **Art. 801** : Pour la modification d'un règlement, il faut la majorité du quorum, soit **50% + 1**.
            * **Art. 802** : Tout directeur général peut présenter des ajouts, annulations ou modifications lors d'une rencontre prévue durant l'année. **Aucune discussion, décision ou modification ayant un impact sur les règles ou les DGs ne peut se faire par Internet.** On doit obligatoirement faire le tout en personne lors de nos rencontres.
            * **Art 803** : Lorsqu'un directeur général est absent d'une réunion, il perd son droit de vote.
            * **Art 804** : Lorsqu'un règlement est adopté, il a une durée de vie **minimum de 2 ans** avant de pouvoir être modifié.
            """)

        with st.expander("🚑 SECTION 9 — JOUEURS BLESSÉS"):
            st.markdown("""
            * **ART 901** : **IL N'Y A AUCUNE RÈGLE POUR JOUEURS BLESSÉS.** *(Pas de liste des blessés distincte)*.
            """)

        with st.expander("⏰ ARTICLES 1000 À 1002 — GESTION DE FIN DE SAISON & PARTANTS"):
            st.markdown("""
            * **Art 1000** : Après la fermeture des transactions, le secrétaire du pool fera tous les changements de tous les DGs. La seule fois où le secrétaire consultera un DG sera quand plus de deux joueurs auront le même nombre de points (le DG tranchera). **Tous les meilleurs pointeurs devront obligatoirement être partants et les plus faibles sur le banc.**
            * **Art 1001** : Durant la saison régulière, chaque DG a l'entière liberté d'effectuer ses changements de partants à volonté (selon les quotas).
            * **Art 1002** : **À LA FIN DE LA SAISON, LE SECRÉTAIRE SE CHARGERA DE METTRE LES MEILLEURS POINTEURS DE TOUS SUR LES PARTANTS ET LES MOINS BONS SUR LE BANC.**
            """)

    with st.expander("🏢 SECTION 11 — EXPANSION, ÉQUIPE DISSOUTE & FORMULE DE PROTECTION", expanded=True):
        st.markdown("""
        * **Art 1101** : Lors du départ d'un DG, on essaie toujours de lui trouver un remplaçant en lui transmettant l'équipe telle quelle. Si aucun remplaçant n'est trouvé, on applique les **Art. 1103 et 1105**.
        * **Art 1103** : Lorsqu'un DG démissionne et que personne ne le remplace, il y a un repêchage de dispersion effectué dans son équipe par les autres directeurs généraux. **Le DG ayant terminé dernier repêche en premier, l'avant-dernier en 2e, etc.**
        * **Art 1104 — EXPANSION DANS LE POOL :**
          * Chaque équipe doit protéger **UN POURCENTAGE DE JOUEURS EN FONCTION DU CLASSEMENT FINAL** (toutes positions confondues).
          * Parmi les joueurs protégés, il doit obligatoirement y avoir au moins **2 gardiens ayant joué un minimum de 20 matchs** dans la saison précédente laissés **SANS PROTECTION**.
          * La nouvelle équipe doit piger **10 joueurs** (toutes positions confondues) dans chaque concession existante (donc 80 joueurs au total).
          * La nouvelle équipe pige toujours dernière au repêchage amateur. Le nouveau DG ne peut pas prendre plus d'un gardien par équipe parmi ses 10 sélections.
          * Après avoir acquis ses joueurs et complété le repêchage annuel, le titulaire pige dans la banque de recrues ou les agents libres disponibles pour compléter sa banque.
          * Chaque année suivant une expansion, on ajoute 10 rondes supplémentaires pour compenser les équipes ayant perdu des joueurs.
        """)

        st.markdown(f"""
        <div style='background: rgba(15,23,42,0.8); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 15px; margin: 15px 0;'>
            <h4 style='color: {t['primary']}; margin-top:0;'>📋 TABLEAU OFFICIEL DES POURCENTAGES DE PROTECTION (EXPANSION)</h4>
            <div style='display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; font-size: 0.95rem;'>
                <div style='background: rgba(0,0,0,0.3); padding: 8px 12px; border-radius: 6px;'>🥇 <b>1ère place :</b> 35% de joueurs protégés</div>
                <div style='background: rgba(0,0,0,0.3); padding: 8px 12px; border-radius: 6px;'>🥈 <b>2e place :</b> 40% de joueurs protégés</div>
                <div style='background: rgba(0,0,0,0.3); padding: 8px 12px; border-radius: 6px;'>🥉 <b>3e place :</b> 45% de joueurs protégés</div>
                <div style='background: rgba(0,0,0,0.3); padding: 8px 12px; border-radius: 6px;'>🏅 <b>4e place :</b> 50% de joueurs protégés</div>
                <div style='background: rgba(0,0,0,0.3); padding: 8px 12px; border-radius: 6px;'>🏅 <b>5e place :</b> 55% de joueurs protégés</div>
                <div style='background: rgba(0,0,0,0.3); padding: 8px 12px; border-radius: 6px;'>🏅 <b>6e place :</b> 60% de joueurs protégés</div>
                <div style='background: rgba(0,0,0,0.3); padding: 8px 12px; border-radius: 6px;'>🏅 <b>7e à 12e place :</b> 65% de joueurs protégés</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("""
        * **Art 1105 — DISSOLUTION D'UN CLUB :**
          * Après avoir cherché un candidat, si un vote de réduire le nombre de participants est accepté par les DGs, on choisit une date pour dissoudre le club en question.
          * Pour la dissolution, le classement général inversé est appliqué pour le repêchage des joueurs : **le draft s'effectue de 11 à 1, et la 2e ronde s'effectue également de 11 à 1** (ordre inversé continu).
          * Les choix de repêchage sont aussi mis dans la dissolution s'il y a lieu *(ex: 13 choix disponibles pour 2026 et 14 choix pour 2027)*.
        """)

# =========================================================
# ONGLET 4 : CHOIX DE REPÊCHAGE & ENCAN COMPLET
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
    
    path_excel = get_file_path("gestion_phdm.xlsx")
    excel_draft_found = False
    
    if os.path.exists(path_excel):
        try:
            xls = pd.ExcelFile(path_excel)
            draft_sheets = [s for s in xls.sheet_names if "repechage" in clean_str(s) or "draft" in clean_str(s)]
            
            if draft_sheets:
                excel_draft_found = True
                draft_sheets.sort()
                
                with c_ctrl1: 
                    onglet_choisi = st.radio("Sélectionnez la cohorte :", draft_sheets, horizontal=True, label_visibility="collapsed")
                with c_ctrl2: 
                    e_filtre = st.selectbox("🔎 Mettre en valeur (DG) :", ["Toutes les équipes"] + list(TEAM_ABBR_MAP.values()), label_visibility="collapsed", key="filt_draft_excel")
                
                m_yr = re.search(r'(2026|2027|2028)', str(onglet_choisi))
                annee_cohorte = m_yr.group(1) if m_yr else "2026"
                
                df_raw = pd.read_excel(path_excel, sheet_name=onglet_choisi, header=None)
                
                header_idx = 0
                for idx, row in df_raw.head(10).iterrows():
                    row_vals = [str(x).upper().strip() for x in row.values]
                    if sum(1 for t in row_vals if to_team_abbr(t) is not None) >= 3 or any("RONDE" in v for v in row_vals):
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
                        new_cols.append(val_str.upper() if val_str and val_str.upper() not in ['NAN', 'NONE'] else f"Col_{i}")
                
                df_draft_excel = df_raw.iloc[header_idx+1:].copy()
                df_draft_excel.columns = new_cols
                df_draft_excel = df_draft_excel.dropna(how='all').reset_index(drop=True)
                
                for col in df_draft_excel.columns:
                    df_draft_excel[col] = df_draft_excel[col].astype(str).str.replace(r'\.0$', '', regex=True).str.strip().str.upper()
                    df_draft_excel[col] = df_draft_excel[col].replace(['NAN', 'NONE', ''], '')

                # --- CARTE DE POSSESSION DES CHOIX ---
                draft_ownership_map = {}
                
                # 1. Lecture de la matrice Excel
                for idx_r, row_d in df_draft_excel.iterrows():
                    m_r = re.search(r'\d+', str(row_d.get("Ronde", "")))
                    rd_num = int(m_r.group(0)) if m_r else (idx_r + 1)
                    for orig_abbr in TEAM_ABBR_MAP.values():
                        if orig_abbr in df_draft_excel.columns:
                            owner = extract_owner_abbr(row_d[orig_abbr])
                            if owner: draft_ownership_map[(rd_num, orig_abbr)] = owner

                # 2. Synchronisation avec les transactions en mémoire
                k_mem = f"df_draft_{annee_cohorte}"
                if k_mem in st.session_state:
                    df_mem = st.session_state[k_mem]
                    for r_label, r_series in df_mem.iterrows():
                        m_r = re.search(r'\d+', str(r_label))
                        rd_num = int(m_r.group(0)) if m_r else 1
                        for orig_col, mem_val in r_series.items():
                            slot_abbr = to_team_abbr(orig_col)
                            owner_abbr = extract_owner_abbr(mem_val)
                            if slot_abbr and owner_abbr:
                                if owner_abbr != slot_abbr:
                                    draft_ownership_map[(rd_num, slot_abbr)] = owner_abbr
                                elif (rd_num, slot_abbr) not in draft_ownership_map:
                                    draft_ownership_map[(rd_num, slot_abbr)] = slot_abbr

                # 3. Attribution par défaut sécurisée
                total_rounds = max(15, len(df_draft_excel))
                for rd_num in range(1, total_rounds + 1):
                    for orig_abbr in TEAM_ABBR_MAP.values():
                        if (rd_num, orig_abbr) not in draft_ownership_map:
                            draft_ownership_map[(rd_num, orig_abbr)] = orig_abbr

                # 4. REGLE OFFICIELLE : Application stricte des choix transigés par MCW pour 2026
                if annee_cohorte in ["2026", "26"] or "2026" in str(onglet_choisi):
                    draft_ownership_map[(3, "MCW")] = "NYR"
                    draft_ownership_map[(6, "MCW")] = "CHI"

                # 5. Synchronisation de la grille Excel avec les transactions pour l'affichage matriciel
                for (rd_num, orig_abbr), owner_abbr in draft_ownership_map.items():
                    if orig_abbr in df_draft_excel.columns:
                        for idx_r, row_d in df_draft_excel.iterrows():
                            m_r = re.search(r'\d+', str(row_d.get("Ronde", "")))
                            r_num = int(m_r.group(0)) if m_r else (idx_r + 1)
                            if r_num == rd_num:
                                df_draft_excel.at[idx_r, orig_abbr] = owner_abbr
                                break

                # --- COMPTABILISATION DES CHOIX ---
                all_owned_picks = list(draft_ownership_map.values())
                counts = pd.Series(all_owned_picks).value_counts()
                
                st.markdown("---")
                
                # --- BADGES DE CAPITAL DE REPÊCHAGE ---
                titre_dash = "CAPITAL DE REPÊCHAGE GLOBAL" if e_filtre == "Toutes les équipes" else f"CAPITAL DE REPÊCHAGE : {e_filtre}"
                st.markdown(f"<h4 style='color:{t['primary']}; font-weight:900; text-transform:uppercase; margin-bottom:15px; font-size:1.05rem; letter-spacing:1px;'>📊 {titre_dash} ({onglet_choisi.upper()})</h4>", unsafe_allow_html=True)
                
                badges_list = []
                for team_abbr in TEAM_ABBR_MAP.values(): 
                    count = counts.get(team_abbr, 0)
                    info = DRAFT_TEAM_STYLES.get(team_abbr, {"bg": "#334155", "color": "#FFFFFF", "border": "#475569"})
                    opacity = "1.0"
                    if e_filtre != "Toutes les équipes" and team_abbr != e_filtre:
                        opacity = "0.25"
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

                # =========================================================
                # 1. DÉROULEMENT OFFICIEL DE L'ENCAN (CLASSEMENT INVERSÉ)
                # =========================================================
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
                    ronde_selectionnee = st.selectbox("🎯 **SÉLECTIONNER LA RONDE À AFFICHER :**", options_rondes, key="choix_ronde_box")

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

                # =========================================================
                # 2. GRILLE MATRICIELLE DU FICHIER EXCEL
                # =========================================================
                st.markdown(f"<h3 style='color:{t['primary']}; font-weight:900; margin: 20px 0 10px 0;'>📊 GRILLE MATRICIELLE DU FICHIER EXCEL ({onglet_choisi.upper()})</h3>", unsafe_allow_html=True)
                
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

                styler = df_draft_excel.style
                try: styler = styler.map(premium_draft_style)
                except AttributeError: styler = styler.applymap(premium_draft_style)
                
                cols_config = {col: st.column_config.TextColumn(col, alignment="center") for col in df_draft_excel.columns}
                st.dataframe(styler, use_container_width=True, hide_index=True, height=580, column_config=cols_config)

        except PermissionError: 
            st.error("⚠️ Le fichier Excel est actuellement ouvert ! Fermez Microsoft Excel puis rafraîchissez la page.")
        except Exception as e: 
            st.error(f"⚠️ Erreur lors de la lecture de l'onglet dans Excel : {e}")
            
    if not excel_draft_found:
        st.warning("⚠️ Aucun onglet de repêchage trouvé dans `gestion_phdm.xlsx`.")

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
        
        CONF_REC = {"No": st.column_config.NumberColumn("No", width="small", alignment="center"), "Joueurs": st.column_config.TextColumn("Nom du Joueur", width="medium"), "Pos": st.column_config.TextColumn("Pos", width="small", alignment="center"), "Année": st.column_config.TextColumn("Année", width="small", alignment="center"), "Ronde": st.column_config.TextColumn("Ronde", width="small", alignment="center"), "Rang": st.column_config.TextColumn("Rang", width="small", alignment="center"), "Cotes": st.column_config.TextColumn("Cotes Experts", width="small", alignment="center"), "Club Junior": st.column_config.TextColumn("Club Junior", width="medium"), "Ligue": st.column_config.TextColumn("Ligue", width="small", alignment="center"), "Équipe LNH (Réelle)": None, "Équipe PHDM": st.column_config.TextColumn("Appartient à (PHDM)", width="medium", alignment="center")}

        if filtre != "Toutes les recrues":
            df_show = df_show[df_show["Équipe LNH (Réelle)"] == filtre]
            logo, color = get_nhl_team_branding(filtre)
            st.markdown(f'''<div style="display:flex;align-items:center;background:rgba(30,41,59,0.7);backdrop-filter:blur(10px);padding:12px;border-left:8px solid {color};border-radius:12px;margin:10px 0 15px;box-shadow:0 4px 15px rgba(0,0,0,0.3);"><img src="{logo}" width="55" style="margin-right: 20px; filter: drop-shadow(0px 0px 5px rgba(255,255,255,0.2));"><div><h3 style="margin: 0; color: #F8FAFC; text-transform: uppercase; font-weight:900;">{filtre}</h3><div style="color: {t['primary']}; font-weight:700; font-size: 0.9rem; letter-spacing:1px;">ESPOIRS REPÊCHÉS ({len(df_show)} JOUEURS)</div></div></div>''', unsafe_allow_html=True)
            st.dataframe(df_show.style.apply(lambda r: ['background-color: transparent; color: #F8FAFC'] * len(r), axis=1), hide_index=True, use_container_width=True, height=650, column_config=CONF_REC)
        else:
            st.markdown(f"<h3 style='color:{t['primary']}; font-weight:900;'>TOUTES LES RECRUES ({len(df_show)} joueurs)</h3>", unsafe_allow_html=True)
            for eq in vrais_lnh:
                df_eq = df_show[df_show["Équipe LNH (Réelle)"] == eq]
                if df_eq.empty: continue
                logo, color = get_nhl_team_branding(eq)
                st.markdown(f'''<div style="display:flex;align-items:center;background:rgba(15,23,42,0.6);padding:10px 15px;border-left:6px solid {color};border-radius:8px;margin:30px 0 10px;box-shadow:0 2px 10px rgba(0,0,0,0.2);"><img src="{logo}" width="40" style="margin-right: 15px; filter: drop-shadow(0px 0px 3px rgba(255,255,255,0.1));"><h4 style="margin: 0; color: #F8FAFC; font-weight:800; text-transform: uppercase; letter-spacing:1px;">{eq} <span style="color:{t['primary']}; font-size:0.9rem;">({len(df_eq)})</span></h4></div>''', unsafe_allow_html=True)
                st.dataframe(df_eq.style.apply(lambda r: ['background-color: transparent; color: #E2E8F0'] * len(r), axis=1), hide_index=True, use_container_width=True, column_config=CONF_REC
